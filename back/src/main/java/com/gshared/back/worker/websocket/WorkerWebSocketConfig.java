package com.gshared.back.worker.websocket;

import lombok.RequiredArgsConstructor;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.web.socket.config.annotation.EnableWebSocket;
import org.springframework.web.socket.config.annotation.WebSocketConfigurer;
import org.springframework.web.socket.config.annotation.WebSocketHandlerRegistry;

/**
 * Worker WebSocket 조립.
 * "/ws/workers 로 들어오면 → 경비원(인터셉터) 검사 후 → 교환원(핸들러)이 처리"
 */
@Configuration
@EnableWebSocket
@EnableScheduling   // 하트비트 타임아웃 순찰(WorkerHeartbeatSweeper)용
@RequiredArgsConstructor
public class WorkerWebSocketConfig implements WebSocketConfigurer {

    private final WorkerWebSocketHandler workerWebSocketHandler;
    private final WorkerHandshakeInterceptor workerHandshakeInterceptor;

    @Override
    public void registerWebSocketHandlers(WebSocketHandlerRegistry registry) {
        registry.addHandler(workerWebSocketHandler, "/ws/workers")
                .addInterceptors(workerHandshakeInterceptor)
                // 워커는 네이티브 클라이언트(브라우저 아님) → Origin 검사 불필요. 인증은 토큰으로.
                .setAllowedOrigins("*");
    }
}
