package com.gshared.back.worker.websocket;

import com.gshared.back.worker.websocket.protocol.WorkerMessage;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.TextMessage;
import tools.jackson.databind.ObjectMapper;

/**
 * 백엔드 → 워커 능동 발신기.
 * workerId 만 주면 전화번호부(레지스트리)에서 연결을 찾아 봉투를 보낸다.
 * (JOB_ASSIGN / JOB_CANCEL 등 백엔드가 먼저 말 거는 메시지용)
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class WorkerMessageSender {

    private final WorkerSessionRegistry sessionRegistry;
    private final ObjectMapper objectMapper;

    /**
     * @return 전송 성공 여부. 연결이 없거나(오프라인) 전송 실패면 false.
     */
    public boolean send(String workerId, WorkerMessage message) {
        return sessionRegistry.find(workerId)
                .filter(session -> session.isOpen())
                .map(session -> {
                    try {
                        session.sendMessage(new TextMessage(objectMapper.writeValueAsString(message)));
                        return true;
                    } catch (Exception e) {
                        log.warn("[WS] 발신 실패 workerId={} type={}", workerId, message.type(), e);
                        return false;
                    }
                })
                .orElseGet(() -> {
                    log.warn("[WS] 발신 불가 - 연결 없음 workerId={} type={}", workerId, message.type());
                    return false;
                });
    }
}
