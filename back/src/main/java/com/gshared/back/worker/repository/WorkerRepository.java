package com.gshared.back.worker.repository;

import com.gshared.back.worker.domain.Worker;
import com.gshared.back.worker.domain.WorkerStatus;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

public interface WorkerRepository extends JpaRepository<Worker, String> {

    // WebSocket 연결 시 Bearer 토큰(SHA-256 해시)으로 Worker 식별
    Optional<Worker> findByWorkerToken(String workerTokenHash);

    // 하트비트 타임아웃 스위퍼: OFFLINE 아닌데 일정 시간 하트비트 없는 워커
    List<Worker> findByStatusNotAndLastHeartbeatAtBefore(WorkerStatus status, LocalDateTime threshold);

    /**
     * 내 워커 목록. owner 를 함께 읽는 것은 응답에 지갑 연결 여부가 들어가기 때문이다 —
     * LAZY 로 두면 워커 수만큼 추가 쿼리가 나간다(N+1).
     * 최근 등록 순: 방금 붙인 PC 가 맨 위에 보여야 확인이 쉽다.
     */
    @Query("""
            SELECT w FROM Worker w
              JOIN FETCH w.owner o
             WHERE o.id = :ownerId
             ORDER BY w.createdAt DESC
            """)
    List<Worker> findMineWithOwner(Long ownerId);

    /** 소유권 확인용 — 남의 워커를 지우거나 들여다볼 수 없게 한다. */
    Optional<Worker> findByIdAndOwnerId(String id, Long ownerId);

    /**
     * 매칭용: 배정 가능한 워커 목록.
     * ONLINE 이면서, 이미 맡은 Job(ASSIGNED/RUNNING)이 없는 워커만 — 이중 배정 방지.
     * 하트비트 최신 순(확실히 살아있는 순).
     *
     * <p>owner 를 함께 읽는 이유는 예치할 때 넣을 공급자 지갑 주소가 owner 에 있기 때문이다.
     * ponytail: 단순 선택. AI Scheduler GPU 추천 연동 시 이 쿼리를 교체
     */
    @Query("""
            SELECT w FROM Worker w
              JOIN FETCH w.owner
             WHERE w.status = com.gshared.back.worker.domain.WorkerStatus.ONLINE
               AND w.id NOT IN (
                   SELECT j.assignedWorkerId FROM Job j
                    WHERE j.assignedWorkerId IS NOT NULL
                      AND j.status IN (com.gshared.back.job.domain.JobStatus.ASSIGNED,
                                       com.gshared.back.job.domain.JobStatus.RUNNING))
             ORDER BY w.lastHeartbeatAt DESC
            """)
    List<Worker> findAvailableWorkers();
}
