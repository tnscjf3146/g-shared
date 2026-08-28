package com.gpucompute.poc.backend.websocket;

import java.util.concurrent.TimeUnit;

import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.client.standard.StandardWebSocketClient;
import org.springframework.web.socket.handler.TextWebSocketHandler;

import com.gpucompute.poc.backend.GpuComputeBackendApplication;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest(
    classes = GpuComputeBackendApplication.class,
    webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT
)
class WebSocketEndpointIntegrationTest {

    @LocalServerPort
    private int port;

    @Test
    void acceptsRawWebSocketHandshakeAtWorkerEndpoint() throws Exception {
        StandardWebSocketClient client = new StandardWebSocketClient();
        WebSocketSession session = client
            .execute(new TextWebSocketHandler() { }, "ws://127.0.0.1:" + port + "/ws/workers")
            .get(5, TimeUnit.SECONDS);

        try {
            assertThat(session.isOpen()).isTrue();
        } finally {
            session.close();
        }
    }
}
