package com.gshared.back.job.repository;

import com.gshared.back.job.domain.Job;
import com.gshared.back.job.domain.JobStatus;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.LocalDateTime;
import java.util.Optional;

public interface JobRepository extends JpaRepository<Job, Long> {

    // 본인 Job 목록 (페이지네이션) — 상태 필터 유무 두 가지
    Page<Job> findByOwnerId(Long ownerId, Pageable pageable);

    Page<Job> findByOwnerIdAndStatus(Long ownerId, JobStatus status, Pageable pageable);

    // 본인 Job 단건 (소유권 확인까지 한 번에)
    Optional<Job> findByIdAndOwnerId(Long id, Long ownerId);

    // 매칭 순찰용: 배정 대기(FUNDED) Job을 오래된 순(선착순)으로
    java.util.List<Job> findByStatusOrderByCreatedAtAsc(JobStatus status);

    /**
     * 원자적 취소: "취소 가능한 상태일 때만" UPDATE 가 한 방에 실행된다.
     * 확인→변경 사이 틈(경쟁 조건)이 없고, 동시 이중 취소도 한쪽만 성공(영향 행 수로 판별).
     * @return 영향받은 행 수 (1=취소 성공, 0=상태가 이미 바뀌어 취소 불가)
     */
    @Modifying(clearAutomatically = true)
    @Query("""
            UPDATE Job j
               SET j.status = com.gshared.back.job.domain.JobStatus.CANCELLED,
                   j.finishedAt = :now
             WHERE j.id = :jobId
               AND j.owner.id = :ownerId
               AND j.status IN (com.gshared.back.job.domain.JobStatus.CREATED,
                                com.gshared.back.job.domain.JobStatus.FUNDED,
                                com.gshared.back.job.domain.JobStatus.ASSIGNED)
            """)
    int cancelIfCancellable(@Param("jobId") Long jobId, @Param("ownerId") Long ownerId, @Param("now") LocalDateTime now);

    /**
     * 원자적 시작(JOB_ACCEPTED): "이 워커에게 배정된 ASSIGNED 상태일 때만" RUNNING 전이.
     * 엉뚱한 워커의 메시지·중복 메시지는 조건 불일치로 0행 → 무시된다.
     */
    @Modifying(clearAutomatically = true)
    @Query("""
            UPDATE Job j
               SET j.status = com.gshared.back.job.domain.JobStatus.RUNNING,
                   j.startedAt = :now
             WHERE j.id = :jobId
               AND j.assignedWorkerId = :workerId
               AND j.status = com.gshared.back.job.domain.JobStatus.ASSIGNED
            """)
    int startIfAssigned(@Param("jobId") Long jobId, @Param("workerId") String workerId, @Param("now") LocalDateTime now);

    /** JOB_PROGRESS: "이 워커의 RUNNING Job"일 때만 진행률 갱신. */
    @Modifying(clearAutomatically = true)
    @Query("""
            UPDATE Job j
               SET j.progressPercent = :percent,
                   j.progressEpoch = :epoch,
                   j.progressTotalEpochs = :totalEpochs
             WHERE j.id = :jobId
               AND j.assignedWorkerId = :workerId
               AND j.status = com.gshared.back.job.domain.JobStatus.RUNNING
            """)
    int updateProgressIfRunning(@Param("jobId") Long jobId, @Param("workerId") String workerId,
                                @Param("percent") Integer percent, @Param("epoch") Integer epoch,
                                @Param("totalEpochs") Integer totalEpochs);

    /** JOB_RESULT: "이 워커의 RUNNING Job"일 때만 COMPLETED 전이 + 결과 기록. */
    @Modifying(clearAutomatically = true)
    @Query("""
            UPDATE Job j
               SET j.status = com.gshared.back.job.domain.JobStatus.COMPLETED,
                   j.resultHash = :resultHash,
                   j.resultUrl = :resultUrl,
                   j.actualRuntimeSec = :actualRuntimeSec,
                   j.finishedAt = :now
             WHERE j.id = :jobId
               AND j.assignedWorkerId = :workerId
               AND j.status = com.gshared.back.job.domain.JobStatus.RUNNING
            """)
    int completeIfRunning(@Param("jobId") Long jobId, @Param("workerId") String workerId,
                          @Param("resultHash") String resultHash, @Param("resultUrl") String resultUrl,
                          @Param("actualRuntimeSec") Integer actualRuntimeSec, @Param("now") LocalDateTime now);

    /** JOB_FAILED: "이 워커의 RUNNING Job"일 때만 FAILED 전이 + 사유 기록. */
    @Modifying(clearAutomatically = true)
    @Query("""
            UPDATE Job j
               SET j.status = com.gshared.back.job.domain.JobStatus.FAILED,
                   j.failReason = :failReason,
                   j.finishedAt = :now
             WHERE j.id = :jobId
               AND j.assignedWorkerId = :workerId
               AND j.status = com.gshared.back.job.domain.JobStatus.RUNNING
            """)
    int failIfRunning(@Param("jobId") Long jobId, @Param("workerId") String workerId,
                      @Param("failReason") String failReason, @Param("now") LocalDateTime now);

    /**
     * 원자적 배정(매칭): "아직 FUNDED 일 때만" ASSIGNED 전이.
     * 매칭이 Job 을 읽은 뒤 사용자가 취소해도, 조건 불일치(0행)로 취소를 되살리지 않는다.
     * 트랜잭션 경계는 서비스 계층(JobService.assignJob)에서 관리.
     */
    @Modifying(clearAutomatically = true)
    @Query("""
            UPDATE Job j
               SET j.status = com.gshared.back.job.domain.JobStatus.ASSIGNED,
                   j.assignedWorkerId = :workerId
             WHERE j.id = :jobId
               AND j.status = com.gshared.back.job.domain.JobStatus.FUNDED
            """)
    int assignIfFunded(@Param("jobId") Long jobId, @Param("workerId") String workerId);
}
