package com.gshared.back.job.service;

import com.gshared.back.dataset.domain.Dataset;
import com.gshared.back.dataset.exception.DatasetErrorCode;
import com.gshared.back.dataset.repository.DatasetRepository;
import com.gshared.back.global.client.SchedulerClient;
import com.gshared.back.global.client.dto.SchedulerRecommendRequest;
import com.gshared.back.global.client.dto.SchedulerRecommendResponse;
import com.gshared.back.global.client.dto.SchedulerTrainingConfig;
import com.gshared.back.global.exception.BusinessException;
import com.gshared.back.job.domain.Job;
import com.gshared.back.job.dto.request.CreateJobRequest;
import com.gshared.back.job.dto.response.CreateJobResponse;
import com.gshared.back.job.domain.JobStatus;
import com.gshared.back.workload.exception.WorkloadErrorCode;
import com.gshared.back.workload.repository.BaseModelRepository;
import com.gshared.back.workload.repository.WorkloadRepository;
import com.gshared.back.job.dto.response.JobDetailResponse;
import com.gshared.back.job.dto.response.JobPageResponse;
import com.gshared.back.job.dto.response.JobSummaryResponse;
import com.gshared.back.job.exception.JobErrorCode;
import com.gshared.back.job.repository.JobRepository;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import com.gshared.back.user.domain.User;
import com.gshared.back.user.exception.UserErrorCode;
import com.gshared.back.user.repository.UserRepository;
import com.gshared.back.worker.repository.WorkerRepository;
import com.gshared.back.worker.websocket.WorkerMessageSender;
import com.gshared.back.global.blockchain.EscrowVerification;
import com.gshared.back.global.blockchain.EscrowVerification.Verdict;
import com.gshared.back.worker.websocket.protocol.WorkerMessage;
import com.gshared.back.worker.websocket.protocol.WorkerMessageType;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;

import java.time.LocalDateTime;
import java.time.Instant;
import java.util.Set;

@Slf4j
@Service
@RequiredArgsConstructor
@Transactional(readOnly = true)
public class JobService {

    /**
     * JOB_REJECTED 중 "다시 시도하면 될 수 있는" 사유 (규격 §14). 이 코드면 배정을 되돌려 다음 순찰에 재매칭하고,
     * 나머지(가격·주소·상태 불일치 등)는 같은 워커에게 다시 줘도 같은 답이라 FAILED 로 확정한다.
     */
    private static final Set<String> RETRYABLE_REJECT_CODES = Set.of("BLOCKCHAIN_RPC_FAILED");
    private static final int FAIL_REASON_MAX = 2000;   // 폭주 메시지 상한 (DB는 TEXT지만 무한 저장 방지)

    private final JobRepository jobRepository;
    private final EscrowVerification escrow;
    private final UserRepository userRepository;
    private final WorkerRepository workerRepository;
    private final DatasetRepository datasetRepository;
    private final WorkloadRepository workloadRepository;
    private final BaseModelRepository baseModelRepository;
    private final SchedulerClient schedulerClient;
    private final TrainingConfigResolver trainingConfigResolver;
    private final WorkerMessageSender messageSender;
    private final ObjectMapper objectMapper;

    /**
     * Job(주문서) 생성 = 견적 발급. CREATED 행이 곧 견적서.
     *
     * 의도적으로 메서드 트랜잭션 없음 — Scheduler(외부 HTTP) 호출을 트랜잭션 안에 두면
     * DB 커넥션을 쥔 채 응답을 기다리게 된다. 쓰기는 마지막 save() 한 번뿐이라 원자성도 충분.
     */
    public CreateJobResponse create(Long ownerId, CreateJobRequest request) {
        // ① 컨텍스트 로드 (각 조회는 짧은 자체 트랜잭션)
        User owner = userRepository.findById(ownerId)
                .orElseThrow(() -> new BusinessException(UserErrorCode.USER_NOT_FOUND));
        Dataset dataset = datasetRepository.findByIdAndOwnerId(request.datasetId(), ownerId)
                .orElseThrow(() -> new BusinessException(DatasetErrorCode.DATASET_NOT_FOUND));
        workloadRepository.findByIdAndEnabledTrue(request.workloadId())
                .orElseThrow(() -> new BusinessException(WorkloadErrorCode.WORKLOAD_NOT_FOUND));
        var baseModel = baseModelRepository.findByIdAndEnabledTrue(request.baseModelId())
                .orElseThrow(() -> new BusinessException(WorkloadErrorCode.BASE_MODEL_NOT_FOUND));
        // 모델이 요청한 이미지(workload)에 속하는지 — 불일치 조합이 Worker 까지 흘러가지 않게
        if (!baseModel.getWorkload().getId().equals(request.workloadId())) {
            throw new BusinessException(WorkloadErrorCode.BASE_MODEL_NOT_FOUND);
        }

        SchedulerRecommendRequest.DatasetMeta datasetMeta = new SchedulerRecommendRequest.DatasetMeta(
                dataset.getSizeBytes(), dataset.getSampleCount(),
                dataset.getTotalTokens(), dataset.getEffectiveTokens(),
                dataset.getAvgSeqLength(), dataset.getMaxSeqLength());

        // ② 학습 설정 확정 — CUSTOM 은 사용자 값 검증, AUTO 는 Scheduler 가 결정 (트랜잭션 밖)
        SchedulerTrainingConfig config = resolveTrainingConfig(request, datasetMeta);

        // ③ GPU 후보 (규격 §28) — 운영 조건(연결·하트비트·미배정·operator/지갑)은 쿼리가 걸렀고,
        //    스펙(null 필드) 없는 워커만 여기서 더 뺀다 (Scheduler 가 null 을 422 로 거절)
        var candidates = workerRepository.findAvailableWorkers().stream()
                .filter(w -> w.getGpuModel() != null && w.getVramMb() != null
                        && w.getBenchmarkTokensPerSec() != null)
                // 공급자가 이 operator 키를 온체인에서 승인했는가. 승인하지 않은
                // 워커에게 배정하면 acceptJob 이 되돌려지고, 그 사이 고객의
                // 예치금은 묶여 있다. 배정 전에 거르는 편이 싸다.
                //
                // 모를 때는 거르지 않는다 -- 체인이 설정되지 않았거나 노드가 잠깐
                // 답하지 않는 동안 멀쩡한 공급자가 후보에서 조용히 빠지면 Job 은
                // 배정되지 않고 아무도 이유를 모른다.
                .filter(w -> !escrow.operatorApproved(
                        w.getOwner().getWalletAddress(), w.getOperatorAddress()).isContradicted())
                .map(w -> new SchedulerRecommendRequest.GpuCandidate(
                        w.getId(), w.getGpuModel(), w.getVramMb(), w.getBenchmarkTokensPerSec()))
                .toList();
        if (candidates.isEmpty()) {
            throw new BusinessException(JobErrorCode.NO_AVAILABLE_WORKER);   // 409 — 기다리면 해결
        }

        // ④ Scheduler 추천 (트랜잭션 밖 — 외부 HTTP)
        SchedulerRecommendResponse rec = schedulerClient.recommend(new SchedulerRecommendRequest(
                request.baseModelId(), request.workloadId(), datasetMeta, config, candidates));
        // 2xx 지만 추천이 비어있는 경우 — "실행 가능한 GPU 없음"으로 명확히 (미매핑 500 방지)
        if (rec.recommendedWorkerId() == null || rec.rankings() == null || rec.rankings().isEmpty()) {
            throw new BusinessException(com.gshared.back.global.client.SchedulerErrorCode.NO_FEASIBLE_GPU);
        }

        // ⑤ 견적 저장 — 예측·비용은 전부 서버가 계산한 값 (클라이언트 값 안 믿음)
        Job job = Job.create(
                owner, request.workloadId(), request.baseModelId(), request.datasetId(),
                request.trainingMode(),
                config.epochs(), config.batchSize(), config.learningRate(), config.maxLength(),
                recommendedRuntime(rec), rec.schedulerModelVersion(), rec.recommendedWorkerId(),
                estimateCost(dataset, config)
        );
        jobRepository.save(job);

        return CreateJobResponse.from(job);
    }

    /**
     * CUSTOM: 사용자 4값 검증 / AUTO: Scheduler 가 방향(trainingPriority)에 맞춰 확정.
     *
     * <p>실제 규칙은 {@link TrainingConfigResolver} 에 있다 — 견적(estimate)과 같은 규칙이어야
     * 하기 때문이다. 견적에서 통과한 설정이 주문에서 거절당하면 사용자는 무엇이 틀렸는지 알 수 없다.
     */
    private SchedulerTrainingConfig resolveTrainingConfig(CreateJobRequest request,
                                                          SchedulerRecommendRequest.DatasetMeta datasetMeta) {
        return trainingConfigResolver.resolve(
                request.trainingMode(), request.trainingPriority(),
                request.trainingConfig(), datasetMeta);
    }

    /** 추천 워커의 예상 실행시간 (rankings 에서 추출, 없으면 1등). */
    private Double recommendedRuntime(SchedulerRecommendResponse rec) {
        return rec.rankings().stream()
                .filter(r -> r.workerId().equals(rec.recommendedWorkerId()))
                .findFirst()
                .orElse(rec.rankings().get(0))
                .predictedRuntimeSec();
    }

    /**
     * 예상 비용 (TOKEN). 공식은 {@link JobCostPolicy} 에 있다 —
     * 견적 화면에서 본 금액과 실제 주문 금액이 갈라지지 않게 한 곳에서만 계산한다.
     */
    private Long estimateCost(Dataset dataset, SchedulerTrainingConfig config) {
        return JobCostPolicy.estimate(dataset.getEffectiveTokens(), config.epochs());
    }

    /**
     * Job 단건 조회. 본인 Job만 조회 가능(소유권 확인).
     * findByIdAndOwnerId 로 "id가 42면서 동시에 주인이 나인" Job을 찾는다 →
     * 남의 Job(id는 맞지만 주인이 다름)은 애초에 안 잡혀서 NOT_FOUND.
     */
    public JobDetailResponse getJob(Long ownerId, Long jobId) {
        Job job = jobRepository.findByIdAndOwnerId(jobId, ownerId)
                .orElseThrow(() -> new BusinessException(JobErrorCode.JOB_NOT_FOUND));
        return JobDetailResponse.from(job);
    }

    /**
     * 본인 Job 목록 조회(페이지네이션). status 가 있으면 그 상태만 필터, 없으면 전체.
     */
    public JobPageResponse getJobs(Long ownerId, JobStatus status, Pageable pageable) {
        Page<Job> jobs = (status == null)
                ? jobRepository.findByOwnerId(ownerId, pageable)
                : jobRepository.findByOwnerIdAndStatus(ownerId, status, pageable);
        return JobPageResponse.from(jobs.map(JobSummaryResponse::from));
    }

    /**
     * 예치 확인 → 매칭 대기(FUNDED)로 전이. 본인 Job이면서 CREATED 상태일 때만 가능.
     * escrowJobId(온체인 jobId)를 함께 기록해 두면 JOB_ASSIGN·execution-context 로 워커에게 전달된다.
     *
     * TODO: 개발용 수동 전이. 최종 구조에서는 FE 가 준 escrowJobId 를 정본으로 쓰지 않고,
     *       Backend 가 JobFunded 이벤트의 externalRef 를 대조해 escrowJobId 를 직접 확정한다.
     * ⚠️ 운영 배포 전 반드시 교체 — 실제 예치 없이 FUNDED 가능한 개발용 API.
     */
    @Transactional
    public void fundJob(Long ownerId, Long jobId, String escrowJobId) {
        // 존재/소유 확인 (없거나 남의 것 → 404)
        jobRepository.findByIdAndOwnerId(jobId, ownerId)
                .orElseThrow(() -> new BusinessException(JobErrorCode.JOB_NOT_FOUND));

        // 체인이 이 예치에 붙인 번호를 정본으로 쓴다. 프런트가 준 값은 브라우저에서
        // 온 숫자이고, JobFunded 로그의 jobId 는 컨트랙트가 붙인 것이다. 확인하지
        // 못했으면(설정 없음·노드 무응답) 받은 값을 쓰되 그 사실을 남긴다.
        String confirmed = escrow.escrowJobIdFor(externalRefOf(jobId)).orElse(null);
        if (confirmed != null && !confirmed.equals(escrowJobId)) {
            log.warn("[JOB] JobFunded 가 말하는 escrowJobId 로 교체한다 jobId={} 신고={} 온체인={}",
                    jobId, escrowJobId, confirmed);
        }
        String authoritative = confirmed != null ? confirmed : escrowJobId;

        // 원자적 전이 — 0행이면 이미 FUNDED거나 취소/진행 중 (→ 409)
        int updated = jobRepository.fundIfCreated(jobId, ownerId, authoritative);
        if (updated == 0) {
            throw new BusinessException(JobErrorCode.INVALID_JOB_STATUS);
        }
    }

    /** 이 Job 의 externalRef. 아직 없으면 null -- 그러면 온체인 조회도 건너뛴다. */
    private String externalRefOf(Long jobId) {
        return jobRepository.findById(jobId).map(Job::getExternalRef).orElse(null);
    }

    /**
     * Job 취소. 본인 Job이면서 종료 전이면 가능하되, 워커 손에 갔는지에 따라 갈린다.
     * <ul>
     *   <li>CREATED/FUNDED → 바로 CANCELLED (아무도 실행하지 않았다)
     *   <li>ASSIGNED/RUNNING → CANCELLING + 워커에게 JOB_CANCEL. 워커가 abortJob() 후 JOB_CANCELLED 로 답해야
     *       CANCELLED 로 확정된다 (규격 §21). 답이 없으면 CANCELLING 에 남아 온체인 대조 대상이 된다.
     * </ul>
     * 두 UPDATE 모두 조건부라, 확인과 변경 사이에 매칭이 배정해 버려도 취소를 되살리거나 놓치지 않는다.
     */
    @Transactional
    public void cancelJob(Long ownerId, Long jobId) {
        // 존재/소유 확인 (없거나 남의 것 → 404)
        jobRepository.findByIdAndOwnerId(jobId, ownerId)
                .orElseThrow(() -> new BusinessException(JobErrorCode.JOB_NOT_FOUND));

        if (jobRepository.cancelIfUnassigned(jobId, ownerId, LocalDateTime.now()) > 0) {
            return;   // 워커 손에 가기 전 — 알릴 상대가 없다
        }
        if (jobRepository.markCancellingIfActive(jobId, ownerId) == 0) {
            throw new BusinessException(JobErrorCode.INVALID_JOB_STATUS);   // 이미 끝났거나 취소 중
        }
        // CANCELLING 확정 "후" 재조회 — clearAutomatically 덕에 DB 최신값(배정 워커)을 읽는다
        jobRepository.findById(jobId)
                .map(Job::getAssignedWorkerId)
                .ifPresent(workerId -> sendCancel(workerId, jobId, "USER_REQUEST"));
        // TODO: FUNDED 취소의 예치금 환불은 의뢰자가 온체인 cancelJob() — BC 연동 후 연결
    }

    /**
     * 매칭: "아직 FUNDED 일 때만" Worker 배정(ASSIGNED). 그 사이 취소됐으면 0행 → false.
     * JobMatcher 가 Job 단위로 호출 — 이 트랜잭션이 커밋된 뒤 JOB_ASSIGN 을 전송한다.
     */
    @Transactional
    public boolean assignJob(Long jobId, String workerId) {
        return jobRepository.assignIfFunded(jobId, workerId) > 0;
    }

    /**
     * 배정 되돌리기(JOB_ASSIGN 전송 실패 시): ASSIGNED → FUNDED 복귀, 다음 순찰에 재매칭.
     * 이미 RUNNING(워커가 수락)이면 0행 — 되돌리지 않는다.
     */
    @Transactional
    public boolean unassignJob(Long jobId, String workerId) {
        return jobRepository.unassignIfAssigned(jobId, workerId) > 0;
    }

    /**
     * JOB_ACCEPTED 수신 (규격 §13): payload 의 escrowJobId·operatorAddress 가 Backend 기록과 같을 때만
     * Job ASSIGNED→RUNNING + acceptJob() 해시 기록 + Worker RUNNING 전이.
     *
     * <p>하나라도 다르면 <b>fail closed</b> — RUNNING 으로 올리지 않고 CANCELLING 으로 보내 워커에게 JOB_CANCEL 을 보내며,
     * 대조 사유를 남긴다. 워커는 이미 온체인에서 accept 했을 수 있으므로 abortJob() 으로 정리해야 한다.
     * TODO: BC 연동 후 tx receipt · 온체인 state == ASSIGNED · job.operator 까지 여기서 검증한다.
     * @return 정상 전이 여부 (불일치·배정 불일치·중복이면 false)
     */
    @Transactional
    public boolean acceptJob(String workerId, Long jobId, String escrowJobId, String operatorAddress, String acceptTxHash) {
        Job job = jobRepository.findByIdAndAssignedWorkerId(jobId, workerId).orElse(null);
        if (job == null || job.getStatus() != JobStatus.ASSIGNED) {
            return false;   // 배정된 적 없거나 이미 시작·취소됨 — 호출자(핸들러)가 로그
        }
        String expectedOperator = workerRepository.findById(workerId)
                .map(worker -> worker.getOperatorAddress()).orElse(null);

        String recorded = acceptMismatch(job, expectedOperator, escrowJobId, operatorAddress);
        // Backend 기록끼리 맞았다면 이제 체인에 같은 것을 묻는다 -- 지금까지는 워커가
        // 보낸 값을 Backend 가 저장해 둔 값과 대조한 것이고, 둘 다 워커에게서 온
        // 것이라 "온체인에서 수락했다"는 주장 자체는 아직 아무것도 확인되지 않았다.
        final String mismatch = recorded != null ? recorded
                : escrow.acceptedOnChain(job.getEscrowJobId(), operatorAddress).isContradicted()
                        ? "on-chain state does not show this job assigned to this operator"
                        : null;
        if (mismatch != null) {
            log.error("[JOB] JOB_ACCEPTED 불일치 → fail closed jobId={} workerId={} {}", jobId, workerId, mismatch);
            boolean cancelling = jobRepository.markCancellingIfAssigned(jobId, workerId) > 0;
            // 조건부 UPDATE 가 영속성 컨텍스트를 비우므로(clearAutomatically) 사유는 그 뒤에 다시 읽어 남긴다
            jobRepository.findById(jobId).ifPresent(fresh ->
                    fresh.flagReconciliation("JOB_ACCEPTED " + mismatch + " (txHash=" + acceptTxHash + ")"));
            if (cancelling) {
                sendCancel(workerId, jobId, "ACCEPT_VALIDATION_FAILED");
            }
            return false;
        }

        if (jobRepository.startIfAssigned(jobId, workerId, acceptTxHash, LocalDateTime.now()) == 0) {
            return false;
        }
        workerRepository.findById(workerId)
                .ifPresent(worker -> worker.startJob(jobId));
        return true;
    }

    /** JOB_ACCEPTED 대조. 어긋난 항목을 문장으로, 전부 맞으면 null. */
    private static String acceptMismatch(Job job, String expectedOperator, String escrowJobId, String operatorAddress) {
        if (job.getEscrowJobId() == null || !job.getEscrowJobId().equals(escrowJobId)) {
            return "escrowJobId mismatch: backend=" + job.getEscrowJobId() + " worker=" + escrowJobId;
        }
        if (expectedOperator == null || operatorAddress == null || !expectedOperator.equalsIgnoreCase(operatorAddress)) {
            return "operatorAddress mismatch: backend=" + expectedOperator + " worker=" + operatorAddress;
        }
        return null;
    }

    /**
     * JOB_REJECTED 수신 (규격 §14): 워커가 acceptJob 전 검증에서 거절.
     * <ul>
     *   <li>BLOCKCHAIN_RPC_FAILED — 일시적. 배정을 되돌려 다음 순찰에 재매칭.
     *   <li>그 외(가격·주소·externalRef 불일치, ESCROW_JOB_NOT_FOUND, ASSIGNMENT_MISMATCH …) — 자동 재매칭하지 않는다.
     *       FAILED 로 닫고 대조 사유를 남긴다: DB 는 끝났지만 온체인 에스크로는 아직 FUNDED 일 수 있어 사람이 봐야 한다.
     * </ul>
     * @return 처리 여부 (이 워커에게 ASSIGNED 인 Job 이 아니면 false)
     */
    @Transactional
    public boolean rejectJob(String workerId, Long jobId, String code, String message) {
        if (code != null && RETRYABLE_REJECT_CODES.contains(code)) {
            return jobRepository.unassignIfAssigned(jobId, workerId) > 0;
        }
        String reason = failReason(code, message);
        if (jobRepository.rejectIfAssigned(jobId, workerId, reason, LocalDateTime.now()) == 0) {
            return false;
        }
        jobRepository.findById(jobId).ifPresent(job ->
                job.flagReconciliation("JOB_REJECTED " + reason + " — 온체인 에스크로 상태 확인 필요"));
        return true;
    }

    /** JOB_PROGRESS 수신 (규격 §16): 진행률 스냅샷 갱신. RUNNING 이 아니면 0행(무시). */
    @Transactional
    public boolean updateProgress(String workerId, Long jobId, Double percent,
                                  Integer step, Integer totalSteps,
                                  Integer epoch, Integer totalEpochs, Integer elapsedSec) {
        return jobRepository.updateProgressIfRunning(jobId, workerId,
                percent, step, totalSteps, epoch, totalEpochs, elapsedSec) > 0;
    }

    /**
     * JOB_RESULT 수신 (규격 §19): resultHash 가 업로드 완료 검증(§18)에서 Backend 가 직접 계산한
     * sha256 에 0x 를 붙인 값과 <b>반드시 같아야</b> COMPLETED 로 전이한다.
     * 다르거나 §18 을 거치지 않았으면 fail closed — 상태를 바꾸지 않고 대조 사유만 남긴다
     * (온체인 submitResult 는 이미 됐을 수 있어 되돌릴 수 없다).
     *
     * <p><b>CANCELLING 도 받는다.</b> 사용자가 취소를 누른 순간 워커는 이미 결과를 올리고
     * 온체인 submitResult 까지 끝냈을 수 있다. RUNNING 만 받으면 그 JOB_RESULT 는 버려지고,
     * 에스크로는 COMPLETED 라 공급자는 정산을 받았는데 Job 은 CANCELLING 에 영원히 남는다.
     * 취소 중에 들어온 경우에도 대조는 똑같이 거치며, reconciliationReason 에 그 사실을
     * 남겨 나중에 사람이 "왜 취소 요청이 완료로 끝났는가"를 알 수 있게 한다.
     *
     * TODO: BC 연동 후 온체인 resultHash 까지 같은지 확인. 정산 트리거는 BC 담당과 협의 후.
     */
    @Transactional
    public boolean completeJob(String workerId, Long jobId, String objectKey,
                               String resultHash, String submitTxHash, Double actualRuntimeSec) {
        Job job = jobRepository.findByIdAndAssignedWorkerId(jobId, workerId).orElse(null);
        if (job == null) {
            return false;
        }
        JobStatus status = job.getStatus();
        if (status != JobStatus.RUNNING && status != JobStatus.CANCELLING) {
            return false;
        }
        String mismatch = resultMismatch(job, objectKey, resultHash);
        if (mismatch != null) {
            log.error("[JOB] JOB_RESULT 불일치 → fail closed jobId={} workerId={} {}", jobId, workerId, mismatch);
            job.flagReconciliation("JOB_RESULT " + mismatch + " (submitTxHash=" + submitTxHash + ")");
            return false;
        }

        // 체인이 정말 COMPLETED 이고, 기록된 resultHash 가 Backend 가 §18 에서 직접
        // 계산한 것과 같은가. 워커가 보낸 resultHash 와 대조하는 것이 아니다 --
        // 그러면 워커의 말을 워커의 말로 검증하는 셈이다.
        String verified = job.getResultSha256() == null ? null : "0x" + job.getResultSha256();
        Verdict onChain = escrow.resultOnChain(job.getEscrowJobId(),
                job.getAcceptTxHash() == null ? null : operatorOf(workerId), verified);
        if (onChain.isContradicted()) {
            log.error("[JOB] JOB_RESULT 온체인 대조 실패 → fail closed jobId={} workerId={}", jobId, workerId);
            job.flagReconciliation("JOB_RESULT on-chain mismatch (submitTxHash=" + submitTxHash + ")");
            return false;
        }

        LocalDateTime now = LocalDateTime.now();
        int updated = status == JobStatus.RUNNING
                ? jobRepository.completeIfRunning(jobId, workerId,
                        objectKey, resultHash, submitTxHash, actualRuntimeSec, now)
                : jobRepository.completeIfCancelling(jobId, workerId,
                        objectKey, resultHash, submitTxHash, actualRuntimeSec,
                        CANCEL_LOST_TO_RESULT, now);
        if (updated == 0) {
            return false;
        }
        if (status == JobStatus.CANCELLING) {
            log.warn("[JOB] 취소 요청과 겹친 JOB_RESULT → COMPLETED jobId={} workerId={}", jobId, workerId);
        }
        workerRepository.findById(workerId).ifPresent(worker -> worker.finishJob());
        return true;
    }

    /**
     * 취소 요청이 결과보다 늦었다는 기록.
     *
     * <p>상태만 보고는 알 수 없는 사실이라 남긴다 — COMPLETED 인 Job 을 나중에 보면
     * 사용자가 취소를 눌렀다는 흔적이 어디에도 없다.
     */
    private static final String CANCEL_LOST_TO_RESULT =
            "사용자 취소 요청이 도착했을 때 결과가 이미 업로드·검증되고 온체인에 기록된 뒤였다. "
            + "에스크로가 COMPLETED 이므로 취소가 아니라 완료로 확정했다.";

    /**
     * 취소 요청 중에 실행이 실패해 워커가 abortJob 으로 정리한 기록.
     *
     * <p>FAILED 가 아니라 CANCELLED 로 끝나는 이유가 여기 적혀 있다.
     */
    private static final String CANCEL_MET_FAILURE =
            "사용자 취소 요청 중에 실행이 실패했고, 워커가 abortJob 으로 예치금을 돌려줬다. "
            + "온체인 상태가 CANCELLED 이므로 CANCELLED 로 확정했다 (실패 사유는 failReason 에 남긴다).";

    /** JOB_RESULT 대조. §18 기록(objectKey·sha256)과 워커 보고값이 어긋난 항목을 문장으로, 전부 맞으면 null. */
    private static String resultMismatch(Job job, String objectKey, String resultHash) {
        if (job.getResultSha256() == null) {
            return "result-upload/complete 검증 기록 없음";
        }
        if (job.getResultObjectKey() == null || !job.getResultObjectKey().equals(objectKey)) {
            return "objectKey mismatch: verified=" + job.getResultObjectKey() + " worker=" + objectKey;
        }
        String expected = "0x" + job.getResultSha256();
        if (resultHash == null || !expected.equalsIgnoreCase(resultHash)) {
            return "resultHash mismatch: expected=" + expected + " worker=" + resultHash;
        }
        return null;
    }

    /**
     * JOB_FAILED 수신 (규격 §20): Job FAILED + 사유·abortJob() 해시·실측 시간 기록, Worker 는 다시 대기(ONLINE).
     *
     * <p><b>CANCELLING 이었으면 FAILED 가 아니라 CANCELLED 로 끝난다.</b> 워커가 실패를
     * 보고했다는 것은 abortJob 으로 예치금을 돌려줬다는 뜻이고 — abortTxHash 가 그 증거다 —
     * 체인은 CANCELLED 다. 사용자는 취소를 요청했고 돈은 돌아갔으므로 CANCELLED 가 양쪽과
     * 맞는 유일한 종료 상태다. 실패 사유는 버리지 않고 failReason 에 그대로 남는다.
     *
     * <p>이것을 받지 않으면 Job 은 CANCELLING 에 영원히 남는다 — 컨테이너는 죽었고 예치금은
     * 돌아갔는데 기록만 취소 중인 상태.
     *
     * TODO: 환불(블록체인) 트리거 — abortJob() 이 환불까지 하므로 Backend 는 흔적만 남기면 될 수 있음. BC 담당과 확인
     */
    @Transactional
    public boolean failJob(String workerId, Long jobId, String code, String message,
                           String abortTxHash, Double actualRuntimeSec) {
        // 실패 보고에는 abortJob 이 따라온다 -- 예치금이 돌아갔다는 주장이다.
        // 체인이 CANCELLED 가 아니라고 말하면 그 주장은 거짓이고, Job 을 종료
        // 상태로 확정하면 고객의 돈이 묶인 채로 기록만 끝난다.
        if (abortTxHash != null && chainContradictsCancellation(jobId, workerId)) {
            return false;
        }

        LocalDateTime now = LocalDateTime.now();
        String reason = failReason(code, message);
        int updated = jobRepository.failIfRunning(jobId, workerId, reason, abortTxHash, actualRuntimeSec, now);
        if (updated == 0) {
            updated = jobRepository.cancelIfFailedWhileCancelling(
                    jobId, workerId, reason, abortTxHash, actualRuntimeSec, CANCEL_MET_FAILURE, now);
            if (updated > 0) {
                log.warn("[JOB] 취소 요청 중 실패 → CANCELLED jobId={} workerId={} reason={}",
                        jobId, workerId, reason);
            }
        }
        if (updated == 0) {
            return false;
        }
        workerRepository.findById(workerId).ifPresent(worker -> worker.finishJob());
        return true;
    }

    /**
     * JOB_CANCELLED 수신 (규격 §21): 워커가 정리(abortJob 포함)를 마쳤다는 답.
     * CANCELLING → CANCELLED 확정 + abortJob() 해시·실측 시간 기록, 워커를 놓아준다(ONLINE).
     * @return 이 워커의 CANCELLING Job 이었는지
     */
    @Transactional
    public boolean confirmCancelled(String workerId, Long jobId, String abortTxHash, Double actualRuntimeSec) {
        if (chainContradictsCancellation(jobId, workerId)) {
            return false;
        }
        int updated = jobRepository.confirmCancelledIfCancelling(
                jobId, workerId, abortTxHash, actualRuntimeSec, LocalDateTime.now());
        if (updated == 0) {
            return false;
        }
        workerRepository.findById(workerId).ifPresent(worker -> worker.finishJob());
        return true;
    }

    /** 워커에게 JOB_CANCEL (규격 §21). payload.reason 은 USER_REQUEST 또는 Backend 가 멈춘 사유. */
    private void sendCancel(String workerId, Long jobId, String reason) {
        ObjectNode payload = objectMapper.createObjectNode();
        payload.put("reason", reason);
        messageSender.send(workerId, WorkerMessage.of(WorkerMessageType.JOB_CANCEL,
                String.valueOf(jobId), Instant.now().toString(), payload));
    }

    /**
     * 체인이 "취소되지 않았다"고 명시적으로 말하는가.
     *
     * <p>모를 때는 막지 않는다. 아직 컨트랙트가 배포되지 않았거나 노드가 잠깐
     * 답하지 않는 것을 근거로 워커의 종료 보고를 버리면, Job 은 CANCELLING 에
     * 남고 워커는 이미 슬롯을 놓았다 -- 아무도 그 Job 을 끝낼 수 없게 된다.
     */
    private boolean chainContradictsCancellation(Long jobId, String workerId) {
        String escrowJobId = jobRepository.findById(jobId)
                .map(Job::getEscrowJobId).orElse(null);
        if (escrowJobId == null) {
            return false;
        }
        Verdict onChain = escrow.cancelledOnChain(escrowJobId);
        if (onChain.isContradicted()) {
            log.error("[JOB] 취소 보고가 온체인과 어긋난다 → 확정하지 않는다 jobId={} workerId={}",
                    jobId, workerId);
            jobRepository.findById(jobId).ifPresent(fresh ->
                    fresh.flagReconciliation("cancellation reported but the escrow is not CANCELLED"));
            return true;
        }
        return false;
    }

    /** 이 워커의 operator 주소. 없으면 null -- 온체인 대조에서 건너뛴다. */
    private String operatorOf(String workerId) {
        return workerRepository.findById(workerId)
                .map(worker -> worker.getOperatorAddress()).orElse(null);
    }

    /** 사유 = "코드: 메시지" 형태로 합쳐 저장 (예: "CUDA_OOM: CUDA out of memory."). */
    private static String failReason(String code, String message) {
        String reason = (code != null ? code : "UNKNOWN") + (message != null ? ": " + message : "");
        return reason.length() > FAIL_REASON_MAX ? reason.substring(0, FAIL_REASON_MAX) : reason;
    }
}
