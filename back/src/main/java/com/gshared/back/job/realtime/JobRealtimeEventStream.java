package com.gshared.back.job.realtime;

import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.event.TransactionPhase;
import org.springframework.transaction.event.TransactionalEventListener;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;

@Slf4j
@Component
public class JobRealtimeEventStream {
    /** 15초 — nginx proxy_read_timeout(3600s)보다 훨씬 짧아 프록시 idle 종료와 무관하고, 끊긴 구독자를 빨리 걷어낸다 */
    private static final long HEARTBEAT_MS = 15_000;

    private final ConcurrentHashMap<Long, CopyOnWriteArrayList<SseEmitter>> emitters = new ConcurrentHashMap<>();

    public SseEmitter subscribe(Long jobId) {
        SseEmitter emitter = new SseEmitter(0L);
        emitters.computeIfAbsent(jobId, ignored -> new CopyOnWriteArrayList<>()).add(emitter);
        emitter.onCompletion(() -> remove(jobId, emitter));
        emitter.onTimeout(() -> remove(jobId, emitter));
        emitter.onError(ignored -> remove(jobId, emitter));
        return emitter;
    }

    @TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT)
    public void publish(JobRealtimeEvent event) {
        CopyOnWriteArrayList<SseEmitter> subscribers = emitters.get(event.jobId());
        if (subscribers == null) {
            return;
        }
        for (SseEmitter emitter : subscribers) {
            try {
                emitter.send(SseEmitter.event().name(event.type()).data(event));
            } catch (IOException | IllegalStateException exception) {
                remove(event.jobId(), emitter);
                log.debug("[JOB-EVENT] disconnected subscriber removed jobId={}", event.jobId());
            }
        }
    }

    /**
     * 살아 있음 신호. SSE 주석(<code>:keepalive</code>)이라 클라이언트는 이벤트로 보지 않고 그냥 버린다.
     *
     * <p>두 가지를 한다.
     * <ul>
     *   <li><b>죽은 구독자 청소</b> — {@code SseEmitter(0L)} 라 onTimeout 이 영원히 안 오고,
     *       이벤트가 안 흐르는 Job(CREATED·FUNDED 상세를 열어 두고 떠난 경우)은 끊김이 감지되지 않아
     *       emitter 가 계속 쌓인다. 여기서 써 보면 끊긴 소켓은 예외를 내므로 그때 걷어낸다.
     *   <li><b>연결 확인</b> — 아무 바이트도 안 흐르면 클라이언트는 "조용히 죽은 연결"과 구분할 수 없다.
     * </ul>
     *
     * <p>순찰 스레드 풀은 {@code spring.task.scheduling.pool.size} 로 늘려 뒀다 —
     * 기본값(1)이면 이 작업이 배정 순찰(JobMatcher)을 막는다.
     */
    @Scheduled(fixedDelay = HEARTBEAT_MS)
    public void heartbeat() {
        emitters.forEach((jobId, subscribers) -> {
            for (SseEmitter emitter : subscribers) {
                try {
                    emitter.send(SseEmitter.event().comment("keepalive"));
                } catch (IOException | IllegalStateException exception) {
                    remove(jobId, emitter);
                    log.debug("[JOB-EVENT] stale subscriber removed by heartbeat jobId={}", jobId);
                }
            }
        });
    }

    private void remove(Long jobId, SseEmitter emitter) {
        emitters.computeIfPresent(jobId, (ignored, subscribers) -> {
            subscribers.remove(emitter);
            return subscribers.isEmpty() ? null : subscribers;
        });
    }
}
