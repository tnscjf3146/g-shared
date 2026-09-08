package com.gshared.back.worker.websocket;

import com.gshared.back.job.service.JobService;
import com.gshared.back.worker.service.WorkerService;
import com.gshared.back.worker.websocket.protocol.WorkerMessage;
import com.gshared.back.worker.websocket.protocol.WorkerMessageType;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.handler.ConcurrentWebSocketSessionDecorator;
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
    // "REGISTER 를 마친 세션" 표시 플래그 (등록 전에 다른 메시지를 보내면 무시)
    public static final String REGISTERED = "registered";
    // 동시 전송 안전 세션(ConcurrentWebSocketSessionDecorator) 보관 키
    private static final String SEND_SESSION = "sendSession";

    // 데코레이터 파라미터: 전송 대기 한계 10초, 버퍼 512KB 초과 시 세션 종료
    private static final int SEND_TIME_LIMIT_MS = 10_000;
    private static final int BUFFER_SIZE_LIMIT = 512 * 1024;

    private final ObjectMapper objectMapper;
    private final WorkerSessionRegistry sessionRegistry;
    private final WorkerService workerService;
    private final JobService jobService;

    /**
     * 워커가 연결되면 호출. (인증은 이미 인터셉터가 통과시킴)
     * 이 시점에 세션을 동시전송 안전 데코레이터로 감싸 attribute 에 보관한다 →
     * 이후 모든 발신(ACK·JOB_ASSIGN 등)이 이 세션을 거쳐 쓰레드 안전해진다.
     */
    @Override
    public void afterConnectionEstablished(WebSocketSession session) {
        String workerId = (String) session.getAttributes().get(WORKER_ID);
        session.getAttributes().put(SEND_SESSION,
                new ConcurrentWebSocketSessionDecorator(session, SEND_TIME_LIMIT_MS, BUFFER_SIZE_LIMIT));
        log.info("[WS] 연결됨 workerId={} sessionId={}", workerId, session.getId());
        // 실제 등록은 워커가 보내는 REGISTER 메시지에서 처리 (명세: 연결 후 REGISTER 전송)
    }

    /** attribute 에 보관해둔 동시전송 안전 세션. (없으면 원본 fallback) */
    private WebSocketSession sendSession(WebSocketSession session) {
        return (WebSocketSession) session.getAttributes().getOrDefault(SEND_SESSION, session);
    }

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
        } catch (tools.jackson.core.JacksonException e) {
            // 예상 범주: 워커가 보낸 메시지가 깨짐 — 워커 측 문제이므로 warn, 연결 유지
            log.warn("[WS] 메시지 파싱 실패(연결 유지) workerId={} payload={}", workerId, message.getPayload(), e);
        } catch (Exception e) {
            // 예상 밖: DB 장애 등 시스템 오류 가능성 — error 로 격상해 묻히지 않게. 연결은 유지
            // (연결을 끊어도 시스템 오류가 해결되지 않고, 워커 이탈만 유발하므로)
            log.error("[WS] 메시지 처리 중 예상치 못한 오류(연결 유지) workerId={} payload={}", workerId, message.getPayload(), e);
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
        // 레지스트리엔 감싼 세션을 넣었으므로 비교도 감싼 세션으로 한다.
        // (재연결 후 옛 세션의 종료 이벤트가 늦게 오면 remove 가 false → 새 연결 건드리지 않음)
        if (sessionRegistry.remove(workerId, sendSession(session))) {
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
        // 레지스트리에는 동시전송 안전 세션을 등록 → 백엔드 능동 발신(JOB_ASSIGN 등)도 안전
        WebSocketSession previous = sessionRegistry.add(workerId, sendSession(session));
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
        Long jobId = parseJobId(msg);
        if (jobId == null) {
            return;
        }
        boolean started = jobService.acceptJob(workerId, jobId);
        if (started) {
            log.info("[WS] JOB_ACCEPTED → RUNNING workerId={} jobId={}", workerId, jobId);
        } else {
            log.warn("[WS] JOB_ACCEPTED 무시(배정 불일치/중복) workerId={} jobId={}", workerId, jobId);
        }
    }

    /** 봉투의 jobId(문자열) → Long. 없거나 숫자가 아니면 null + 경고. */
    private Long parseJobId(WorkerMessage msg) {
        if (msg.jobId() == null) {
            log.warn("[WS] jobId 없는 {} 메시지 무시", msg.type());
            return null;
        }
        try {
            return Long.parseLong(msg.jobId());
        } catch (NumberFormatException e) {
            log.warn("[WS] 잘못된 jobId={} ({} 메시지 무시)", msg.jobId(), msg.type());
            return null;
        }
    }

    private void handleJobProgress(String workerId, WorkerMessage msg) {
        Long jobId = parseJobId(msg);
        if (jobId == null) {
            return;
        }
        boolean applied = jobService.updateProgress(workerId, jobId,
                intOrNull(msg, "progress"),
                intOrNull(msg, "currentEpoch"),
                intOrNull(msg, "totalEpochs"));
        if (applied) {
            log.debug("[WS] JOB_PROGRESS workerId={} jobId={} {}%", workerId, jobId, intOrNull(msg, "progress"));
        } else {
            log.warn("[WS] JOB_PROGRESS 무시(RUNNING 아님) workerId={} jobId={}", workerId, jobId);
        }
    }

    /** payload 에서 정수 필드 꺼내기. 없거나 숫자가 아니면 null. */
    private Integer intOrNull(WorkerMessage msg, String field) {
        if (msg.payload() == null) {
            return null;
        }
        var node = msg.payload().path(field);   // path(): 없어도 예외 대신 missing 노드 반환
        return node.isNumber() ? node.asInt() : null;
    }

    private void handleJobResult(String workerId, WorkerMessage msg) {
        Long jobId = parseJobId(msg);
        if (jobId == null) {
            return;
        }
        boolean completed = jobService.completeJob(workerId, jobId,
                textOrNull(msg, "resultHash"),
                textOrNull(msg, "resultUrl"),
                intOrNull(msg, "actualRuntimeSec"));
        if (completed) {
            log.info("[WS] JOB_RESULT → COMPLETED workerId={} jobId={}", workerId, jobId);
        } else {
            log.warn("[WS] JOB_RESULT 무시(RUNNING 아님) workerId={} jobId={}", workerId, jobId);
        }
    }

    /** payload 에서 문자열 필드 꺼내기. 없으면 null. */
    private String textOrNull(WorkerMessage msg, String field) {
        if (msg.payload() == null) {
            return null;
        }
        var node = msg.payload().path(field);
        return node.isString() ? node.asString() : null;   // Jackson 3: isTextual/asText 의 새 이름
    }

    private void handleJobFailed(String workerId, WorkerMessage msg) {
        Long jobId = parseJobId(msg);
        if (jobId == null) {
            return;
        }
        // 사유 = "에러코드: 메시지" 형태로 합쳐 저장 (예: "GPU_OOM: GPU out of memory")
        String errorCode = textOrNull(msg, "errorCode");
        String message = textOrNull(msg, "message");
        String failReason = (errorCode != null ? errorCode : "UNKNOWN") + (message != null ? ": " + message : "");

        boolean failed = jobService.failJob(workerId, jobId, failReason);
        if (failed) {
            log.info("[WS] JOB_FAILED → FAILED workerId={} jobId={} reason={}", workerId, jobId, failReason);
        } else {
            log.warn("[WS] JOB_FAILED 무시(RUNNING 아님) workerId={} jobId={}", workerId, jobId);
        }
    }

    // ── 공통 헬퍼 ───────────────────────────────────────────────

    /** 봉투를 JSON 으로 직렬화해 워커에게 전송. (동시전송 안전 세션 경유) */
    private void send(WebSocketSession session, WorkerMessage message) {
        try {
            sendSession(session).sendMessage(new TextMessage(objectMapper.writeValueAsString(message)));
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
