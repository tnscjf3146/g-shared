package com.gshared.back.worker.websocket;

import com.gshared.back.worker.service.WorkerService;
import com.gshared.back.worker.websocket.protocol.WorkerMessage;
import com.gshared.back.worker.websocket.protocol.WorkerMessageType;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.handler.TextWebSocketHandler;
import tools.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;

/**
 * Worker WebSocket 교환원.
 * 스프링이 연결/메시지/종료 시점에 아래 메서드들을 자동 호출한다.
 *
 * <p>4-1(현재): 뼈대만 — type 분기 골격. 실제 처리 내용은 다음 스텝에서 채운다.
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class WorkerWebSocketHandler extends TextWebSocketHandler {

    // 세션 attribute 에 저장해둘 workerId 키 (핸드셰이크 인터셉터가 넣어줌 → 부품 5)
    public static final String WORKER_ID = "workerId";

    private final ObjectMapper objectMapper;
    private final WorkerSessionRegistry sessionRegistry;
    private final WorkerService workerService;

    /** 워커가 연결되면 호출. (인증은 이미 인터셉터가 통과시킴) */
    @Override
    public void afterConnectionEstablished(WebSocketSession session) {
        String workerId = (String) session.getAttributes().get(WORKER_ID);
        log.info("[WS] 연결됨 workerId={} sessionId={}", workerId, session.getId());
        // 실제 등록은 워커가 보내는 REGISTER 메시지에서 처리 (명세: 연결 후 REGISTER 전송)
    }

    // "REGISTER 를 마친 세션" 표시 플래그 (등록 전에 다른 메시지를 보내면 무시)
    public static final String REGISTERED = "registered";

    /**
     * 워커가 텍스트 메시지를 보낼 때마다 호출. type 을 보고 분기한다.
     * 잘못된 메시지(깨진 JSON, 모르는 type)가 와도 연결은 유지한다 —
     * 예외가 밖으로 나가면 스프링이 세션을 끊어버리기 때문.
     */
    @Override
    protected void handleTextMessage(WebSocketSession session, TextMessage message) {
        String workerId = (String) session.getAttributes().get(WORKER_ID);
        try {
            WorkerMessage msg = objectMapper.readValue(message.getPayload(), WorkerMessage.class);
            if (msg.type() == null) {
                log.warn("[WS] type 없는 메시지 무시 workerId={}", workerId);
                return;
            }
            // 등록 전에는 REGISTER 만 허용 (명세: 연결 → REGISTER → ONLINE)
            if (msg.type() != WorkerMessageType.REGISTER
                    && session.getAttributes().get(REGISTERED) == null) {
                log.warn("[WS] REGISTER 전 메시지 무시 type={} workerId={}", msg.type(), workerId);
                return;
            }

            switch (msg.type()) {
                case REGISTER      -> handleRegister(session, workerId, msg);
                case HEARTBEAT     -> handleHeartbeat(session, workerId, msg);
                case JOB_ACCEPTED  -> handleJobAccepted(workerId, msg);
                case JOB_PROGRESS  -> handleJobProgress(workerId, msg);
                case JOB_RESULT    -> handleJobResult(workerId, msg);
                case JOB_FAILED    -> handleJobFailed(workerId, msg);
                // 나머지(REGISTER_ACK/JOB_ASSIGN 등)는 백엔드가 "보내는" 타입이라 수신될 일 없음
                default -> log.warn("[WS] 예상치 못한 수신 type={} workerId={}", msg.type(), workerId);
            }
        } catch (Exception e) {
            log.warn("[WS] 메시지 처리 실패(연결 유지) workerId={} payload={}", workerId, message.getPayload(), e);
        }
    }

    /** 연결이 끊기면 호출(정상 종료/네트워크 끊김 모두). */
    @Override
    public void afterConnectionClosed(WebSocketSession session, CloseStatus status) {
        String workerId = (String) session.getAttributes().get(WORKER_ID);
        log.info("[WS] 연결종료 workerId={} status={}", workerId, status);
        if (workerId == null) {
            return;   // REGISTER 전에 끊긴 경우
        }
        // 이 세션이 아직 "현재 연결"일 때만 OFFLINE 처리.
        // (재연결 후 옛 세션의 종료 이벤트가 늦게 오면 remove 가 false → 새 연결 건드리지 않음)
        if (sessionRegistry.remove(workerId, session)) {
            try {
                workerService.markOffline(workerId);
            } catch (Exception e) {
                log.warn("[WS] OFFLINE 처리 실패 workerId={}", workerId, e);
            }
        }
    }

    // ── type 별 처리 ────────────────────────────────────────────
    private void handleRegister(WebSocketSession session, String workerId, WorkerMessage msg) {
        log.info("[WS] REGISTER workerId={}", workerId);
        WebSocketSession previous = sessionRegistry.add(workerId, session);
        closeQuietly(previous);   // 같은 워커의 이전 연결이 남아있으면 정리
        workerService.markOnline(workerId, LocalDateTime.now());
        session.getAttributes().put(REGISTERED, Boolean.TRUE);
        send(session, WorkerMessage.of(WorkerMessageType.REGISTER_ACK, now(), emptyPayload()));
    }

    private void handleHeartbeat(WebSocketSession session, String workerId, WorkerMessage msg) {
        log.debug("[WS] HEARTBEAT workerId={}", workerId);
        workerService.touchHeartbeat(workerId, LocalDateTime.now());
        send(session, WorkerMessage.of(WorkerMessageType.HEARTBEAT_ACK, now(), emptyPayload()));
    }

    private void handleJobAccepted(String workerId, WorkerMessage msg) {
        log.info("[WS] JOB_ACCEPTED workerId={} jobId={}", workerId, msg.jobId()); // TODO(4-3)
    }

    private void handleJobProgress(String workerId, WorkerMessage msg) {
        log.debug("[WS] JOB_PROGRESS workerId={} jobId={}", workerId, msg.jobId()); // TODO(4-3)
    }

    private void handleJobResult(String workerId, WorkerMessage msg) {
        log.info("[WS] JOB_RESULT workerId={} jobId={}", workerId, msg.jobId()); // TODO(4-3)
    }

    private void handleJobFailed(String workerId, WorkerMessage msg) {
        log.info("[WS] JOB_FAILED workerId={} jobId={}", workerId, msg.jobId()); // TODO(4-3)
    }

    // ── 공통 헬퍼 ───────────────────────────────────────────────

    /** 봉투를 JSON 으로 직렬화해 워커에게 전송. */
    private void send(WebSocketSession session, WorkerMessage message) {
        try {
            // ponytail: 여러 쓰레드가 같은 세션에 동시 전송하면 깨질 수 있음.
            //           JOB_ASSIGN 등 백엔드 능동 발신이 늘면 ConcurrentWebSocketSessionDecorator 로 감쌀 것.
            session.sendMessage(new TextMessage(objectMapper.writeValueAsString(message)));
        } catch (IOException e) {
            log.warn("[WS] 전송 실패 sessionId={} type={}", session.getId(), message.type(), e);
        }
    }

    /** 이전(중복) 연결 정리. 실패해도 무시 — 이미 죽은 연결일 수 있음. */
    private void closeQuietly(WebSocketSession session) {
        if (session == null || !session.isOpen()) {
            return;
        }
        try {
            session.close(CloseStatus.NORMAL);
        } catch (IOException e) {
            log.debug("[WS] 이전 세션 close 실패(무시) sessionId={}", session.getId());
        }
    }

    private String now() {
        return OffsetDateTime.now().toString();   // ISO 8601
    }

    private tools.jackson.databind.JsonNode emptyPayload() {
        return objectMapper.createObjectNode();   // {}
    }
}
