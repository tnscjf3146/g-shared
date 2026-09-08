package com.gshared.back.worker.repository;

import com.gshared.back.worker.domain.Worker;
import com.gshared.back.worker.domain.WorkerStatus;
import org.springframework.data.jpa.repository.JpaRepository;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

public interface WorkerRepository extends JpaRepository<Worker, String> {

    // WebSocket 연결 시 Bearer 토큰(SHA-256 해시)으로 Worker 식별
    Optional<Worker> findByWorkerToken(String workerTokenHash);

    // 하트비트 타임아웃 스위퍼: OFFLINE 아닌데 일정 시간 하트비트 없는 워커
    List<Worker> findByStatusNotAndLastHeartbeatAtBefore(WorkerStatus status, LocalDateTime threshold);

    /**
     * 매칭용: 배정 가능한 워커 목록.
     * ONLINE 이면서, 이미 맡은 Job(ASSIGNED/RUNNING)이 없는 워커만 — 이중 배정 방지.
     * 하트비트 최신 순(확실히 살아있는 순).
     * ponytail: 단순 선택. AI Scheduler GPU 추천 연동 시 이 쿼리를 교체
     */
    @org.springframework.data.jpa.repository.Query("""
            SELECT w FROM Worker w
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
