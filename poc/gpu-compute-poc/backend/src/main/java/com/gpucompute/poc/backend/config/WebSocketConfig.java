package com.gpucompute.poc.backend.config;

import org.springframework.context.annotation.Configuration;
import org.springframework.web.socket.config.annotation.EnableWebSocket;
import org.springframework.web.socket.config.annotation.WebSocketConfigurer;
import org.springframework.web.socket.config.annotation.WebSocketHandlerRegistry;

import com.gpucompute.poc.backend.websocket.WorkerWebSocketHandler;

@Configuration
@EnableWebSocket
public class WebSocketConfig implements WebSocketConfigurer {

    private final WorkerWebSocketHandler workerWebSocketHandler;
    private final JobSecurityConfig security;

    public WebSocketConfig(
        WorkerWebSocketHandler workerWebSocketHandler,
        JobSecurityConfig security
    ) {
        this.workerWebSocketHandler = workerWebSocketHandler;
        this.security = security;
    }

    @Override
    public void registerWebSocketHandlers(WebSocketHandlerRegistry registry) {
        registry.addHandler(workerWebSocketHandler, "/ws/workers")
            .addInterceptors(security);
    }
}
