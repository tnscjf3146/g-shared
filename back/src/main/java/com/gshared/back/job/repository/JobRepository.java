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

    /**
     * 원자적 취소: "취소 가능한 상태일 때만" UPDATE 가 한 방에 실행된다.
     * 확인→변경 사이 틈(경쟁 조건)이 없고, 동시 이중 취소도 한쪽만 성공(영향 행 수로 판별).
     * @return 영향받은 행 수 (1=취소 성공, 0=상태가 이미 바뀌어 취소 불가)
     */
    @Modifying
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
}
