package com.gpucompute.poc.backend.integration;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.function.BooleanSupplier;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.client.standard.StandardWebSocketClient;
import org.springframework.web.socket.handler.TextWebSocketHandler;

import com.gpucompute.poc.backend.GpuComputeBackendApplication;
import com.gpucompute.poc.backend.domain.ConnectionStatus;
import com.gpucompute.poc.backend.domain.WorkerStatus;
import com.gpucompute.poc.backend.registry.WorkerRegistry;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest(
    classes = GpuComputeBackendApplication.class,
    webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
    properties = {
        "worker.liveness.timeout=500ms",
        "worker.liveness.scan-interval-ms=25"
    }
)
class WorkerProtocolIntegrationTest {

    @LocalServerPort
    private int port;

    @Autowired
    private WorkerRegistry registry;

    @Test
    void realSocketRegistersHeartbeatsTimesOutAndRequiresReregister() throws Exception {
        ClientHandler firstHandler = new ClientHandler();
        WebSocketSession firstSession = connect(firstHandler);
        firstSession.sendMessage(new TextMessage(registerJson()));
        await(() -> registry.findById("worker-liveness")
            .map(record -> record.connectionStatus() == ConnectionStatus.ONLINE)
            .orElse(false));
        String firstServerSessionId = registry.findById("worker-liveness")
            .orElseThrow()
            .activeSession()
            .getId();

        firstSession.sendMessage(new TextMessage("""
            {"type":"HEARTBEAT","workerId":"worker-liveness","freeVramMb":7600,
             "gpuUtilization":42,"workerStatus":"BUSY"}
            """));
        await(() -> registry.findById("worker-liveness")
            .map(record -> record.workerStatus() == WorkerStatus.BUSY
                && record.gpuUtilization() == 42)
            .orElse(false));

        assertThat(firstHandler.closed.await(2, TimeUnit.SECONDS)).isTrue();
        assertThat(firstHandler.closeStatus.getCode()).isEqualTo(1008);
        await(() -> registry.findById("worker-liveness")
            .map(record -> record.connectionStatus() == ConnectionStatus.OFFLINE
                && record.workerStatus() == WorkerStatus.BUSY)
            .orElse(false));

        ClientHandler secondHandler = new ClientHandler();
        WebSocketSession secondSession = connect(secondHandler);
        try {
            secondSession.sendMessage(new TextMessage(registerJson()));
            await(() -> registry.findById("worker-liveness")
                .map(record -> record.connectionStatus() == ConnectionStatus.ONLINE
                    && record.activeSession() != null
                    && !record.activeSession().getId().equals(firstServerSessionId))
                .orElse(false));
        } finally {
            secondSession.close();
        }
        await(() -> registry.findById("worker-liveness")
            .map(record -> record.connectionStatus() == ConnectionStatus.OFFLINE)
            .orElse(false));
    }

    private WebSocketSession connect(ClientHandler handler) throws Exception {
        return new StandardWebSocketClient()
            .execute(handler, "ws://127.0.0.1:" + port + "/ws/workers")
            .get(2, TimeUnit.SECONDS);
    }

    private static void await(BooleanSupplier condition) throws Exception {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(2);
        while (!condition.getAsBoolean() && System.nanoTime() < deadline) {
            Thread.sleep(10);
        }
        assertThat(condition.getAsBoolean()).isTrue();
    }

    private static String registerJson() {
        return """
            {"type":"REGISTER","workerId":"worker-liveness","gpuModel":"NVIDIA Test GPU",
             "totalVramMb":8188,"freeVramMb":7800,"gpuUtilization":0,
             "cudaAvailable":true,"supportedWorkloads":["EMBEDDING"],
             "hourlyPrice":600,"workerStatus":"IDLE"}
            """;
    }

    private static final class ClientHandler extends TextWebSocketHandler {
        private final CountDownLatch closed = new CountDownLatch(1);
        private volatile CloseStatus closeStatus;

        @Override
        public void afterConnectionClosed(WebSocketSession session, CloseStatus status) {
            closeStatus = status;
            closed.countDown();
        }
    }
}
