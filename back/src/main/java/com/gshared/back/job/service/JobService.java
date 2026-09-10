package com.gshared.back.job.service;

import com.gshared.back.dataset.domain.Dataset;
import com.gshared.back.dataset.exception.DatasetErrorCode;
import com.gshared.back.dataset.repository.DatasetRepository;
import com.gshared.back.global.client.SchedulerClient;
import com.gshared.back.global.client.dto.SchedulerRecommendRequest;
import com.gshared.back.global.client.dto.SchedulerRecommendResponse;
import com.gshared.back.global.client.dto.SchedulerTrainingConfig;
import com.gshared.back.global.client.dto.SchedulerTrainingConfigRequest;
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
import com.gshared.back.worker.websocket.protocol.WorkerMessage;
import com.gshared.back.worker.websocket.protocol.WorkerMessageType;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

@Service
@RequiredArgsConstructor
@Transactional(readOnly = true)
public class JobService {

    private final JobRepository jobRepository;
    private final UserRepository userRepository;
    private final WorkerRepository workerRepository;
    private final DatasetRepository datasetRepository;
    private final WorkloadRepository workloadRepository;
    private final BaseModelRepository baseModelRepository;
    private final SchedulerClient schedulerClient;
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

        // ③ GPU 후보 — 스펙(null 필드) 없는 워커는 제외 (Scheduler 가 null 을 422 로 거절)
        var candidates = workerRepository.findAvailableWorkers().stream()
                .filter(w -> w.getGpuModel() != null && w.getVramMb() != null
                        && w.getBenchmarkTokensPerSec() != null)
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

    /** CUSTOM: 사용자 4값 검증 / AUTO: Scheduler 가 방향(trainingPriority)에 맞춰 확정. */
    private SchedulerTrainingConfig resolveTrainingConfig(CreateJobRequest request,
                                                          SchedulerRecommendRequest.DatasetMeta datasetMeta) {
        if ("CUSTOM".equals(request.trainingMode())) {
            JsonNode c = request.trainingConfig();
            if (c == null || !c.path("epochs").isNumber() || !c.path("batchSize").isNumber()
                    || !c.path("learningRate").isNumber() || !c.path("maxLength").isNumber()) {
                throw new BusinessException(JobErrorCode.INVALID_TRAINING_CONFIG);
            }
            SchedulerTrainingConfig config = new SchedulerTrainingConfig(
                    c.path("epochs").asInt(), c.path("batchSize").asInt(),
                    c.path("learningRate").asDouble(), c.path("maxLength").asInt());
            // 양수 검증 — Scheduler 의 422 에만 기대지 않고 우리도 막는다 (0 epochs → 1 TOKEN 견적 방지)
            if (config.epochs() <= 0 || config.batchSize() <= 0
                    || config.learningRate() <= 0 || config.maxLength() <= 0) {
                throw new BusinessException(JobErrorCode.INVALID_TRAINING_CONFIG);
            }
            return config;
        }
        String priority = request.trainingPriority() != null ? request.trainingPriority() : "BALANCED";
        SchedulerTrainingConfig config = schedulerClient.trainingConfig(
                new SchedulerTrainingConfigRequest(datasetMeta, priority));
        // AUTO 응답 형태 검증 — null 필드가 NOT NULL 컬럼 저장에서 터지지 않게
        if (config.epochs() == null || config.batchSize() == null
                || config.learningRate() == null || config.maxLength() == null) {
            throw new BusinessException(com.gshared.back.global.client.SchedulerErrorCode.SCHEDULER_UNAVAILABLE);
        }
        return config;
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
     * 예상 비용 (TOKEN). billing 기준 = effectiveTokens × epochs.
     * ponytail: 과금 정책 미정 — 1만 토큰당 1 TOKEN 임시 공식. 정책 확정 시 이 메서드만 교체.
     */
    private Long estimateCost(Dataset dataset, SchedulerTrainingConfig config) {
        long billedTokens = dataset.getEffectiveTokens() * config.epochs();
        return Math.max(1, billedTokens / 10_000);
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
     *
     * TODO: 현재는 개발용 수동 전이. BC 연동 시 이 메서드 안에서
     *       온체인 예치 여부를 확인한 뒤 전이하도록 교체한다. (fundedAt 시각 기록도 그때 추가)
     * ⚠️ 운영 배포 전 반드시 교체 — 실제 예치 없이 FUNDED 가능한 개발용 API.
     */
    @Transactional
    public void fundJob(Long ownerId, Long jobId) {
        // 존재/소유 확인 (없거나 남의 것 → 404)
        jobRepository.findByIdAndOwnerId(jobId, ownerId)
                .orElseThrow(() -> new BusinessException(JobErrorCode.JOB_NOT_FOUND));

        // 원자적 전이 — 0행이면 이미 FUNDED거나 취소/진행 중 (→ 409)
        int updated = jobRepository.fundIfCreated(jobId, ownerId);
        if (updated == 0) {
            throw new BusinessException(JobErrorCode.INVALID_JOB_STATUS);
        }
    }

    /**
     * Job 취소. 본인 Job이면서 RUNNING 진입 전(CREATED/FUNDED/ASSIGNED)만 취소 가능.
     * "취소 가능할 때만 바꾸는" 조건부 UPDATE 한 방(원자적)이라,
     * 매칭이 동시에 상태를 올리는 경쟁 상황에서도 안전하다.
     */
    @Transactional
    public void cancelJob(Long ownerId, Long jobId) {
        // 존재/소유 확인 (없거나 남의 것 → 404)
        jobRepository.findByIdAndOwnerId(jobId, ownerId)
                .orElseThrow(() -> new BusinessException(JobErrorCode.JOB_NOT_FOUND));

        // 원자적 취소 — 0행이면 그 사이 상태가 바뀐 것 (→ 409)
        int updated = jobRepository.cancelIfCancellable(jobId, ownerId, java.time.LocalDateTime.now());
        if (updated == 0) {
            throw new BusinessException(JobErrorCode.INVALID_JOB_STATUS);
        }

        // 워커에게 배정돼 있었다면 → "취소됐어" 통지 (JOB_CANCEL).
        // 취소 확정 "후" 재조회 — 확인 시점 스냅샷은 그 사이 매칭이 배정했을 수 있어 못 믿는다.
        // (clearAutomatically 덕에 1차 캐시가 아닌 DB 최신값을 읽음)
        jobRepository.findById(jobId)
                .map(Job::getAssignedWorkerId)
                .ifPresent(workerId -> messageSender.send(workerId,
                        WorkerMessage.of(WorkerMessageType.JOB_CANCEL,
                                String.valueOf(jobId),
                                java.time.OffsetDateTime.now().toString(),
                                objectMapper.createObjectNode())));
        // TODO: 예치금 환불 트리거(블록체인 연동) — BC 담당과 협의 후
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
     * JOB_ACCEPTED 수신: Job ASSIGNED→RUNNING + Worker RUNNING 전이.
     * 조건부 UPDATE 라 엉뚱한 워커의 메시지·중복 메시지는 0행으로 무시된다.
     * @return 정상 전이 여부
     */
    @Transactional
    public boolean acceptJob(String workerId, Long jobId) {
        int updated = jobRepository.startIfAssigned(jobId, workerId, java.time.LocalDateTime.now());
        if (updated == 0) {
            return false;   // 배정된 적 없거나 이미 시작됨 — 호출자(핸들러)가 로그
        }
        workerRepository.findById(workerId)
                .ifPresent(worker -> worker.startJob(jobId));
        return true;
    }

    /** JOB_PROGRESS 수신: 진행률 스냅샷 갱신. RUNNING 이 아니면 0행(무시). */
    @Transactional
    public boolean updateProgress(String workerId, Long jobId, Integer percent, Integer epoch, Integer totalEpochs) {
        return jobRepository.updateProgressIfRunning(jobId, workerId, percent, epoch, totalEpochs) > 0;
    }

    /**
     * JOB_RESULT 수신: Job COMPLETED + 결과 기록, Worker 는 다시 대기(ONLINE).
     * TODO: 정산(블록체인 지급) 트리거 — BC 담당과 협의 후
     */
    @Transactional
    public boolean completeJob(String workerId, Long jobId,
                               String resultHash, String resultUrl, Double actualRuntimeSec) {
        int updated = jobRepository.completeIfRunning(jobId, workerId,
                resultHash, resultUrl, actualRuntimeSec, java.time.LocalDateTime.now());
        if (updated == 0) {
            return false;
        }
        workerRepository.findById(workerId).ifPresent(worker -> worker.finishJob());
        return true;
    }

    /**
     * JOB_FAILED 수신: Job FAILED + 사유 기록, Worker 는 다시 대기(ONLINE).
     * TODO: 환불(블록체인) 트리거 — BC 담당과 협의 후
     */
    @Transactional
    public boolean failJob(String workerId, Long jobId, String failReason) {
        int updated = jobRepository.failIfRunning(jobId, workerId, failReason, java.time.LocalDateTime.now());
        if (updated == 0) {
            return false;
        }
        workerRepository.findById(workerId).ifPresent(worker -> worker.finishJob());
        return true;
    }

}
