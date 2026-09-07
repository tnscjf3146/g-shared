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
}
