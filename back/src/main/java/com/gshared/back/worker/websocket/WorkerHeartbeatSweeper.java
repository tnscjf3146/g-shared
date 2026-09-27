package com.gshared.back.worker.websocket;

import com.gshared.back.worker.service.WorkerService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.util.List;

/**
 * 하트비트 타임아웃 순찰자.
 * 명세: HEARTBEAT 는 30초 주기, 90초 이상 미수신이면 OFFLINE.
 *
 * <p>연결이 "조용히" 죽는 경우(정전, 랜선 뽑힘)는 종료 이벤트가 안 오므로
 * afterConnectionClosed 만으로는 못 잡는다 → 주기 순찰로 잡는다.
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class WorkerHeartbeatSweeper {

    private static final long TIMEOUT_SEC = 90;

    private final WorkerService workerService;
    private final WorkerSessionRegistry sessionRegistry;

    @Scheduled(fixedDelay = 30_000)
    public void sweep() {
        List<String> staleIds = workerService.sweepStaleWorkers(LocalDateTime.now().minusSeconds(TIMEOUT_SEC));
        for (String workerId : staleIds) {
            log.info("[WS] 하트비트 타임아웃 → OFFLINE workerId={}", workerId);
            sessionRegistry.find(workerId).ifPresent(session -> {
                sessionRegistry.remove(workerId, session);
                try {
                    session.close();
                } catch (Exception e) {
                    log.debug("[WS] 타임아웃 세션 close 실패(무시) workerId={}", workerId);
                }
            });
        }
    }
}
