package com.gshared.back.worker.websocket;

import com.gshared.back.global.exception.BusinessException;
import com.gshared.back.worker.service.WorkerService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.HttpStatus;
import org.springframework.http.server.ServerHttpRequest;
import org.springframework.http.server.ServerHttpResponse;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.WebSocketHandler;
import org.springframework.web.socket.server.HandshakeInterceptor;

import java.util.Map;

/**
 * WebSocket 연결(악수) 직전에 실행되는 경비원.
 * Authorization: Bearer &lt;WORKER_TOKEN&gt; 를 검증하고,
 * 통과하면 workerId 를 세션 attribute 에 심어 핸들러가 쓸 수 있게 한다.
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class WorkerHandshakeInterceptor implements HandshakeInterceptor {

    private static final String BEARER_PREFIX = "Bearer ";

    private final WorkerService workerService;

    @Override
    public boolean beforeHandshake(ServerHttpRequest request, ServerHttpResponse response,
                                   WebSocketHandler wsHandler, Map<String, Object> attributes) {
        String authorization = request.getHeaders().getFirst("Authorization");
        if (authorization == null || !authorization.startsWith(BEARER_PREFIX)) {
            log.warn("[WS] 핸드셰이크 거부 - Authorization 헤더 없음");
            response.setStatusCode(HttpStatus.UNAUTHORIZED);
            return false;
        }

        String workerToken = authorization.substring(BEARER_PREFIX.length());
        try {
            String workerId = workerService.resolveWorkerIdByToken(workerToken);
            attributes.put(WorkerWebSocketHandler.WORKER_ID, workerId);   // 핸들러가 꺼내 씀
            return true;
        } catch (BusinessException e) {
            log.warn("[WS] 핸드셰이크 거부 - 유효하지 않은 토큰");
            response.setStatusCode(HttpStatus.UNAUTHORIZED);
            return false;
        }
    }

    @Override
    public void afterHandshake(ServerHttpRequest request, ServerHttpResponse response,
                              WebSocketHandler wsHandler, Exception exception) {
        // 후처리 필요 없음
    }
}
