package com.gshared.back.job.service;

import com.gshared.back.dataset.domain.Dataset;
import com.gshared.back.dataset.exception.DatasetErrorCode;
import com.gshared.back.dataset.repository.DatasetRepository;
import com.gshared.back.global.exception.BusinessException;
import com.gshared.back.global.storage.ObjectStorage;
import com.gshared.back.global.storage.StorageProperties;
import com.gshared.back.job.domain.Job;
import com.gshared.back.job.domain.JobStatus;
import com.gshared.back.job.dto.request.DatasetUploadCompleteRequest;
import com.gshared.back.job.dto.request.DatasetUploadRequest;
import com.gshared.back.job.dto.response.DatasetUploadCompleteResponse;
import com.gshared.back.job.dto.response.DatasetUploadResponse;
import com.gshared.back.job.exception.JobErrorCode;
import com.gshared.back.job.repository.JobRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Locale;

/**
 * 데이터셋 원본 파일의 수명을 Job 에 묶어 관리한다.
 *
 * <p>Dataset 메타(datasets 행)는 남고, 원본 파일은 이 Job 을 돌리는 동안만 스토리지에 있다:
 * <pre>
 *   Job 생성(CREATED) → 업로드 URL 발급 → 프런트가 PUT → 완료 검증 → /fund 허용
 *   → 워커가 execution-context 의 downloadUrl 로 받아감 → Job 종료 → cleanup 이 삭제
 * </pre>
 * 흐름은 결과물 업로드(규격 §17·§18)와 같다 — 서명 URL 발급, 클라이언트가 직접 PUT, Backend 가 오브젝트를 보고 확정.
 * 프런트는 objectKey 를 만들지 않는다. Backend 가 {@code jobs/{jobId}/input/…} 로 정하고 프런트는 받은 값을 돌려준다.
 */
@Slf4j
@Service
@RequiredArgsConstructor
@Transactional(readOnly = true)
public class JobDatasetService {

    /** 예치 없이 이만큼 지난 CREATED Job 의 파일은 버려진 견적으로 보고 지운다. 다시 올리면 된다. */
    static final Duration ABANDONED_AFTER = Duration.ofHours(24);

    private final JobRepository jobRepository;
    private final DatasetRepository datasetRepository;
    private final ObjectStorage objectStorage;
    private final StorageProperties storageProperties;

    /** 업로드 URL 발급. 예치 전(CREATED)에만 — 예치 뒤에 파일을 바꾸면 워커가 견적과 다른 데이터를 받는다. */
    public DatasetUploadResponse createUpload(Long ownerId, Long jobId, DatasetUploadRequest request) {
        Job job = findCreatedJob(ownerId, jobId);
        String objectKey = datasetPrefix(job.getId()) + request.fileName();
        ObjectStorage.SignedUrl signed = objectStorage.presignUpload(
                objectKey, request.contentType(), storageProperties.uploadTtl());
        return new DatasetUploadResponse(objectKey, signed.url(), signed.expiresAt());
    }

    /**
     * 업로드 완료. 신고값을 믿지 않고 오브젝트가 실제로 있는지, 크기·해시가 맞는지 확인한 뒤 Job 에 기록한다.
     * 등록된 Dataset 메타(sizeBytes·dataHash)와도 대조한다 — 견적·과금은 그 메타로 계산했으니 올린 파일이 그 파일이어야 한다.
     * 같은 Job 에 다시 올리면(재업로드) 마지막 검증값으로 덮어쓴다.
     */
    @Transactional
    public DatasetUploadCompleteResponse completeUpload(Long ownerId, Long jobId, DatasetUploadCompleteRequest request) {
        Job job = findCreatedJob(ownerId, jobId);
        if (!request.objectKey().startsWith(datasetPrefix(job.getId()))) {
            throw new BusinessException(JobErrorCode.DATASET_OBJECT_KEY_MISMATCH);   // 남의 Job 경로를 내 파일이라 우기지 못하게
        }

        ObjectStorage.ObjectStat stat = objectStorage.stat(request.objectKey())
                .orElseThrow(() -> new BusinessException(JobErrorCode.DATASET_OBJECT_NOT_FOUND));
        String reported = request.sha256().toLowerCase(Locale.ROOT);
        if (stat.sizeBytes() != request.sizeBytes() || !stat.sha256().equals(reported)) {
            log.warn("[Dataset] 업로드 불일치 jobId={} 신고 size={} sha256={} / 실제 size={} sha256={}",
                    jobId, request.sizeBytes(), reported, stat.sizeBytes(), stat.sha256());
            throw new BusinessException(JobErrorCode.DATASET_UPLOAD_MISMATCH);
        }

        Dataset dataset = datasetRepository.findById(job.getDatasetId())
                .orElseThrow(() -> new BusinessException(DatasetErrorCode.DATASET_NOT_FOUND));
        String expectedSha256 = stripHexPrefix(dataset.getDataHash());
        if (stat.sizeBytes() != dataset.getSizeBytes()
                || (expectedSha256 != null && !expectedSha256.equals(stat.sha256()))) {
            log.warn("[Dataset] 메타 불일치 jobId={} datasetId={} 메타 size={} sha256={} / 파일 size={} sha256={}",
                    jobId, dataset.getId(), dataset.getSizeBytes(), expectedSha256, stat.sizeBytes(), stat.sha256());
            throw new BusinessException(JobErrorCode.DATASET_METADATA_MISMATCH);
        }

        job.recordDatasetUpload(request.objectKey(), stat.sizeBytes(), stat.sha256(), LocalDateTime.now());
        return new DatasetUploadCompleteResponse(job.getId(), request.objectKey(), true);
    }

    /** 더는 필요 없는 원본(끝난 Job·방치된 CREATED Job)을 가진 Job 목록. 순찰({@link JobDatasetCleanup})이 부른다. */
    public List<Job> findFilesToClean() {
        return jobRepository.findDatasetFilesToClean(LocalDateTime.now().minus(ABANDONED_AFTER));
    }

    /** 원본 하나 삭제 + 기록. 순찰이 Job 마다 따로 부른다 — 한 파일의 I/O 실패가 나머지를 막지 않게. */
    @Transactional
    public void deleteDatasetFile(Long jobId) {
        Job job = jobRepository.findById(jobId).orElse(null);
        if (job == null || !job.hasDatasetFile()) {
            return;   // 그 사이 지워졌거나 다시 올라온 것 — 이번 순찰은 건너뛴다
        }
        objectStorage.delete(job.getDatasetObjectKey());
        job.markDatasetDeleted(LocalDateTime.now());
        log.info("[Dataset] 원본 삭제 jobId={} status={} key={}", jobId, job.getStatus(), job.getDatasetObjectKey());
    }

    // ── 내부 ─────────────────────────────────────────────────────

    private Job findCreatedJob(Long ownerId, Long jobId) {
        Job job = jobRepository.findByIdAndOwnerId(jobId, ownerId)
                .orElseThrow(() -> new BusinessException(JobErrorCode.JOB_NOT_FOUND));
        if (job.getStatus() != JobStatus.CREATED) {
            throw new BusinessException(JobErrorCode.INVALID_JOB_STATUS);
        }
        return job;
    }

    /** 원본은 Job 별 고정 경로 아래에만 둔다 — 발급도 완료 검증도 cleanup 도 이 접두어로 묶인다. */
    static String datasetPrefix(Long jobId) {
        return "jobs/" + jobId + "/input/";
    }

    /** datasets.data_hash 는 0x 붙은 bytes32 표기, 파일 검증은 0x 없는 64자 — 맞춰서 비교한다. */
    private static String stripHexPrefix(String dataHash) {
        if (dataHash == null || dataHash.isBlank()) {
            return null;
        }
        String hex = dataHash.startsWith("0x") || dataHash.startsWith("0X") ? dataHash.substring(2) : dataHash;
        return hex.toLowerCase(Locale.ROOT);
    }
}
