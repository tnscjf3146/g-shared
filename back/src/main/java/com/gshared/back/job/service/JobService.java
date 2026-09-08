package com.gshared.back.job.service;

import com.gshared.back.global.exception.BusinessException;
import com.gshared.back.job.domain.Job;
import com.gshared.back.job.dto.request.CreateJobRequest;
import com.gshared.back.job.dto.response.CreateJobResponse;
import com.gshared.back.job.domain.JobStatus;
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
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;

@Service
@RequiredArgsConstructor
@Transactional(readOnly = true)
public class JobService {

    private final JobRepository jobRepository;
    private final UserRepository userRepository;
    private final WorkerRepository workerRepository;

    /**
     * 견적 승인 후 Job(주문서) 생성. 생성 직후 상태는 CREATED(예치 확인 대기).
     */
    @Transactional
    public CreateJobResponse create(Long ownerId, CreateJobRequest request) {
        User owner = userRepository.findById(ownerId)
                .orElseThrow(() -> new BusinessException(UserErrorCode.USER_NOT_FOUND));

        Job job = Job.create(
                owner,
                request.workloadId(), request.baseModelId(), request.datasetId(), request.gpuId(),
                request.trainingMode(), toJsonString(request.trainingConfig()),
                request.predictedRuntimeSec(), request.estimatedCost()
        );
        jobRepository.save(job);

        return CreateJobResponse.from(job);
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
        // TODO: 예치금 환불 트리거(블록체인 연동) — BC 담당과 협의 후
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
                .ifPresent(worker -> worker.startJob(String.valueOf(jobId)));
        return true;
    }

    /** JSON 객체 → 저장용 문자열. null(AUTO)이면 그대로 null. */
    private String toJsonString(JsonNode node) {
        return node == null || node.isNull() ? null : node.toString();
    }
}
