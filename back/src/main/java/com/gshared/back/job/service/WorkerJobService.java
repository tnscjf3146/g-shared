package com.gshared.back.job.service;

import com.gshared.back.dataset.domain.Dataset;
import com.gshared.back.dataset.exception.DatasetErrorCode;
import com.gshared.back.dataset.repository.DatasetRepository;
import com.gshared.back.global.blockchain.EscrowProperties;
import com.gshared.back.global.blockchain.TokenUnits;
import com.gshared.back.global.exception.BusinessException;
import com.gshared.back.global.storage.ObjectStorage;
import com.gshared.back.global.storage.StorageProperties;
import com.gshared.back.global.wallet.WalletAuthProperties;
import com.gshared.back.job.domain.Job;
import com.gshared.back.job.domain.JobStatus;
import com.gshared.back.job.dto.request.ResultUploadCompleteRequest;
import com.gshared.back.job.dto.request.ResultUploadRequest;
import com.gshared.back.job.dto.response.ExecutionContextResponse;
import com.gshared.back.job.dto.response.ResultUploadCompleteResponse;
import com.gshared.back.job.dto.response.ResultUploadResponse;
import com.gshared.back.job.exception.JobErrorCode;
import com.gshared.back.job.repository.JobRepository;
import com.gshared.back.worker.domain.Worker;
import com.gshared.back.worker.service.WorkerService;
import com.gshared.back.workload.domain.BaseModel;
import com.gshared.back.workload.domain.Workload;
import com.gshared.back.workload.exception.WorkloadErrorCode;
import com.gshared.back.workload.repository.BaseModelRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.Locale;
import java.util.Set;

/**
 * 워커(System)가 워커 토큰으로 부르는 Job 창구 (규격 §11, §17, §18).
 * 사용자(JWT)가 부르는 {@link JobService} 와 인증 주체가 달라 따로 둔다 —
 * 여기서는 "이 토큰의 워커에게 배정된 Job 인가"가 모든 검증의 출발점이다.
 */
@Slf4j
@Service
@RequiredArgsConstructor
@Transactional(readOnly = true)
public class WorkerJobService {

    /** 온체인 JobState.FUNDED — 워커는 acceptJob 전에 상태가 정확히 이 값인지 확인한다 (§12). */
    private static final int ESCROW_STATE_FUNDED = 1;
    /** Execution Context 를 줄 수 있는 상태. 배정 전(FUNDED)이나 끝난 Job 은 워커가 볼 이유가 없다. */
    private static final Set<JobStatus> CONTEXT_VISIBLE = Set.of(JobStatus.ASSIGNED, JobStatus.RUNNING);

    private final JobRepository jobRepository;
    private final DatasetRepository datasetRepository;
    private final BaseModelRepository baseModelRepository;
    private final WorkerService workerService;
    private final ObjectStorage objectStorage;
    private final StorageProperties storageProperties;
    private final WalletAuthProperties walletAuthProperties;
    private final EscrowProperties escrowProperties;

    /**
     * Execution Context (§11). Backend 는 반드시 {@code authenticated workerId == job.assignedWorkerId} 를 확인한다 —
     * 다른 워커에게 배정된 Job 은 존재 여부도 흘리지 않고 404 다.
     */
    public ExecutionContextResponse getExecutionContext(String rawToken, Long jobId) {
        Worker worker = workerService.authenticateByToken(rawToken);
        Job job = findAssignedJob(jobId, worker);
        if (!CONTEXT_VISIBLE.contains(job.getStatus())) {
            throw new BusinessException(JobErrorCode.INVALID_JOB_STATUS);
        }

        BaseModel model = baseModelRepository.findById(job.getBaseModelId())
                .orElseThrow(() -> new BusinessException(WorkloadErrorCode.BASE_MODEL_NOT_FOUND));
        Workload image = model.getWorkload();
        Dataset dataset = datasetRepository.findById(job.getDatasetId())
                .orElseThrow(() -> new BusinessException(DatasetErrorCode.DATASET_NOT_FOUND));

        return new ExecutionContextResponse(
                String.valueOf(job.getId()),
                job.getAssignedWorkerId(),
                new ExecutionContextResponse.Model(model.getId(), model.getModelRef(), model.getModelRevision()),
                new ExecutionContextResponse.RuntimeImage(image.getId(), imageRef(image), image.getImageDigest()),
                new ExecutionContextResponse.Dataset(
                        dataset.getId(), datasetDownloadUrl(job), job.getDatasetSha256(),   // 해시는 업로드 완료 때 실제 파일에서 계산한 값
                        dataset.getSizeBytes(), dataset.getSampleCount(),
                        dataset.getTotalTokens(), dataset.getEffectiveTokens(),
                        dataset.getAvgSeqLength(), dataset.getMaxSeqLength()),
                new ExecutionContextResponse.TrainingConfig(
                        job.getEpochs(), job.getBatchSize(), job.getLearningRate(), job.getMaxLength()),
                new ExecutionContextResponse.Escrow(
                        // 예치 확인 때 Job 에 박아 둔 체인·컨트랙트. 에스크로를 재배포해도 이 Job 은 예전 주소에 있다.
                        // 없으면(예치 확인 전 데이터) 지금 설정값.
                        job.getEscrowChainId() != null ? job.getEscrowChainId() : walletAuthProperties.chainId(),
                        job.getEscrowContractAddress() != null
                                ? job.getEscrowContractAddress() : escrowProperties.contractAddressOrNull(),
                        job.getEscrowJobId(),
                        job.getExternalRef(),
                        job.getOwner().getWalletAddress(),      // client = 예치한 의뢰자
                        worker.getOwner().getWalletAddress(),   // provider = 공급자 정산 주소
                        job.getEstimatedCost(),
                        TokenUnits.toWei(job.getEstimatedCost()).toString(),
                        ESCROW_STATE_FUNDED)
        );
    }

    /**
     * 결과물 업로드 URL 발급 (§17). 학습이 끝난 RUNNING Job 만 — 그 전에는 올릴 결과가 없고,
     * 끝난 뒤(COMPLETED)에 다시 올리게 두면 제출된 해시와 다른 파일로 바꿔치기할 수 있다.
     */
    public ResultUploadResponse createResultUpload(String rawToken, Long jobId, ResultUploadRequest request) {
        Worker worker = workerService.authenticateByToken(rawToken);
        Job job = findAssignedJob(jobId, worker);
        if (job.getStatus() != JobStatus.RUNNING) {
            throw new BusinessException(JobErrorCode.INVALID_JOB_STATUS);
        }

        String objectKey = resultPrefix(jobId) + request.fileName();
        ObjectStorage.SignedUrl signed = objectStorage.presignUpload(
                objectKey, request.contentType(), storageProperties.uploadTtl());
        return new ResultUploadResponse(objectKey, signed.url(), signed.expiresAt());
    }

    /**
     * 결과물 업로드 완료 (§18). 신고값을 믿지 않고 오브젝트가 실제로 있는지, 크기·해시가 맞는지 확인한 뒤 기록한다.
     * 해시가 맞아야 이후 JOB_RESULT 의 resultHash(= 0x + 이 값)와 온체인 submitResult 가 같은 파일을 가리킨다.
     */
    @Transactional
    public ResultUploadCompleteResponse completeResultUpload(String rawToken, Long jobId,
                                                             ResultUploadCompleteRequest request) {
        Worker worker = workerService.authenticateByToken(rawToken);
        Job job = findAssignedJob(jobId, worker);
        if (job.getStatus() != JobStatus.RUNNING) {
            throw new BusinessException(JobErrorCode.INVALID_JOB_STATUS);
        }
        if (!request.objectKey().startsWith(resultPrefix(jobId))) {
            throw new BusinessException(JobErrorCode.RESULT_OBJECT_KEY_MISMATCH);   // 남의 Job 경로를 내 결과라고 우기지 못하게
        }

        ObjectStorage.ObjectStat stat = objectStorage.stat(request.objectKey())
                .orElseThrow(() -> new BusinessException(JobErrorCode.RESULT_OBJECT_NOT_FOUND));
        String sha256 = request.sha256().toLowerCase(Locale.ROOT);
        if (stat.sizeBytes() != request.sizeBytes() || !stat.sha256().equals(sha256)) {
            log.warn("[Upload] 결과물 불일치 jobId={} 신고 size={} sha256={} / 실제 size={} sha256={}",
                    jobId, request.sizeBytes(), sha256, stat.sizeBytes(), stat.sha256());
            throw new BusinessException(JobErrorCode.RESULT_UPLOAD_MISMATCH);
        }

        // 검증이 끝난 파일은 봉인 — 아직 살아 있는 업로드 서명 URL(TTL 1h)로 온체인 해시와
        // 다른 파일로 바꿔치기하지 못하게. 여기 기록된 해시가 곧 submitResult 의 resultHash 다.
        objectStorage.seal(request.objectKey());
        job.recordResultUpload(request.objectKey(), stat.sizeBytes(), stat.sha256());
        return new ResultUploadCompleteResponse(String.valueOf(jobId), request.objectKey(), true);
    }

    // ── 내부 ─────────────────────────────────────────────────────

    private Job findAssignedJob(Long jobId, Worker worker) {
        return jobRepository.findByIdAndAssignedWorkerId(jobId, worker.getId())
                .orElseThrow(() -> new BusinessException(JobErrorCode.JOB_NOT_FOUND));
    }

    /** 결과물은 Job 별 고정 경로 아래에만 둔다 — 발급도 완료 검증도 이 접두어로 묶는다. */
    private static String resultPrefix(Long jobId) {
        return "jobs/" + jobId + "/";
    }

    /** "le3560/gshared-qlora-sft-runtime@sha256:..." — 저장소가 비어 있으면 digest 만 준다 (풀 수 없는 참조보다는 낫다). */
    private static String imageRef(Workload image) {
        return image.getImageRepository() == null
                ? image.getImageDigest()
                : image.getImageRepository() + "@" + image.getImageDigest();
    }

    /**
     * 데이터셋 내려받기 URL. 원본은 Job 에 묶여 있다(jobs/{id}/input/…, V15) — 업로드 완료 검증을 거친 키로 서명 URL 을 만든다.
     * /fund 가 업로드 전에는 거절하므로 배정된 Job 에는 항상 있어야 하지만, cleanup 이 지웠거나 옛 데이터면 null —
     * 워커는 이 경우 INVALID_INPUT 으로 실패를 알린다.
     */
    private String datasetDownloadUrl(Job job) {
        if (!job.hasDatasetFile()) {
            return null;
        }
        return objectStorage.presignDownload(job.getDatasetObjectKey(), storageProperties.downloadTtl()).url();
    }
}
