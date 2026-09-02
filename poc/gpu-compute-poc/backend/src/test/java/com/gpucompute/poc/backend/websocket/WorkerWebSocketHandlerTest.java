package com.gpucompute.poc.backend.websocket;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;

import tools.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.web.socket.BinaryMessage;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;

import com.gpucompute.poc.backend.domain.ConnectionStatus;
import com.gpucompute.poc.backend.protocol.WorkerMessageDecoder;
import com.gpucompute.poc.backend.protocol.WorkerMessageValidator;
import com.gpucompute.poc.backend.registry.WorkerRecord;
import com.gpucompute.poc.backend.registry.WorkerRegistry;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class WorkerWebSocketHandlerTest {

    private static final Instant NOW = Instant.parse("2026-08-27T00:00:00Z");

    private final WorkerRegistry registry = new WorkerRegistry();
    private final WorkerSessionBindings bindings = new WorkerSessionBindings();
    private final WorkerWebSocketHandler handler = new WorkerWebSocketHandler(
        registry,
        new WorkerMessageDecoder(new ObjectMapper()),
        new WorkerMessageValidator(),
        bindings,
        Clock.fixed(NOW, ZoneOffset.UTC)
    );

    @Test
    void validRegisterBindsSessionAndStoresOnlineWorker() throws Exception {
        WebSocketSession session = openSession("session-A");
        handler.handleMessage(session, new TextMessage(validRegisterJson()));

        WorkerRecord record = registry.findById("worker-001").orElseThrow();
        assertThat(record.connectionStatus()).isEqualTo(ConnectionStatus.ONLINE);
        assertThat(record.activeSession()).isSameAs(session);
        assertThat(record.lastHeartbeat()).isEqualTo(NOW);
        assertThat(bindings.findWorkerId("session-A")).contains("worker-001");
    }

    @Test
    void registerWithBenchmarkStoresBenchmarkInRegistry() throws Exception {
        WebSocketSession session = openSession("session-A");

        handler.handleMessage(session, new TextMessage("""
            {"type":"REGISTER","workerId":"worker-001","gpuModel":"NVIDIA Test GPU",
             "totalVramMb":8188,"freeVramMb":7800,"gpuUtilization":0,
             "cudaAvailable":true,"supportedWorkloads":["GPU_PYTORCH_TEST_30S"],
             "hourlyPrice":600,"workerStatus":"IDLE","benchmark":{
               "version":"gpu-benchmark-v1","score":12.34,
               "throughputTflops":12.34,"matrixSize":4096,"precision":"FP16",
               "measuredAt":"2026-09-01T00:00:00Z"}}
            """));

        WorkerRecord record = registry.findById("worker-001").orElseThrow();
        assertThat(record.benchmark()).isNotNull();
        assertThat(record.benchmark().score()).isEqualTo(12.34);
        assertThat(record.benchmark().measuredAt())
            .isEqualTo(Instant.parse("2026-09-01T00:00:00Z"));
    }

    @Test
    void malformedJsonClosesWithBadDataAndDoesNotRegister() throws Exception {
        WebSocketSession session = openSession("session-A");

        handler.handleMessage(session, new TextMessage("{"));

        verify(session).close(CloseStatus.BAD_DATA);
        assertThat(registry.findAll()).isEmpty();
    }

    @Test
    void invalidRegisterFieldClosesWithBadDataAndDoesNotRegister() throws Exception {
        WebSocketSession session = openSession("session-A");

        handler.handleMessage(session, new TextMessage("""
            {"type":"REGISTER","workerId":"worker-001","gpuModel":"GPU",
             "totalVramMb":0,"freeVramMb":0,"gpuUtilization":0,
             "cudaAvailable":true,"supportedWorkloads":["EMBEDDING"],
             "hourlyPrice":600,"workerStatus":"IDLE"}
            """));

        verify(session).close(CloseStatus.BAD_DATA);
        assertThat(registry.findAll()).isEmpty();
    }

    @Test
    void unknownTypeClosesWithNotAcceptable() throws Exception {
        WebSocketSession session = openSession("session-A");

        handler.handleMessage(session, new TextMessage("{\"type\":\"EXECUTE\"}"));

        verify(session).close(CloseStatus.NOT_ACCEPTABLE);
        assertThat(registry.findAll()).isEmpty();
    }

    @Test
    void secondRegisterOnSameSessionClosesWithPolicyViolation() throws Exception {
        WebSocketSession session = openSession("session-A");
        TextMessage register = new TextMessage(validRegisterJson());
        handler.handleMessage(session, register);

        handler.handleMessage(session, register);

        verify(session).close(CloseStatus.POLICY_VIOLATION);
        assertThat(registry.findById("worker-001").orElseThrow().activeSession())
            .isSameAs(session);
    }

    @Test
    void binaryFrameClosesWithNotAcceptable() throws Exception {
        WebSocketSession session = openSession("session-A");

        handler.handleMessage(session, new BinaryMessage(new byte[] {1}));

        verify(session).close(CloseStatus.NOT_ACCEPTABLE);
    }

    @Test
    void authenticatedTokenCannotRegisterAnotherWorkerId() throws Exception {
        WebSocketSession session = openSession("session-A");
        when(session.getAttributes()).thenReturn(java.util.Map.of(
            com.gpucompute.poc.backend.config.JobSecurityConfig.AUTHENTICATED_WORKER_ID,
            "worker-002"
        ));

        handler.handleMessage(session, new TextMessage(validRegisterJson()));

        verify(session).close(CloseStatus.POLICY_VIOLATION);
        assertThat(registry.findById("worker-001")).isEmpty();
    }

    @Test
    void activeHeartbeatUpdatesRegistryFromSameSession() throws Exception {
        WebSocketSession session = openSession("session-A");
        handler.handleMessage(session, new TextMessage(validRegisterJson()));

        handler.handleMessage(session, new TextMessage("""
            {"type":"HEARTBEAT","workerId":"worker-001","freeVramMb":7600,
             "gpuUtilization":84,"workerStatus":"BUSY"}
            """));

        WorkerRecord record = registry.findById("worker-001").orElseThrow();
        assertThat(record.freeVramMb()).isEqualTo(7600);
        assertThat(record.gpuUtilization()).isEqualTo(84);
        assertThat(record.workerStatus().name()).isEqualTo("BUSY");
        assertThat(record.connectionStatus()).isEqualTo(ConnectionStatus.ONLINE);
    }

    @Test
    void heartbeatBeforeRegisterClosesWithPolicyViolation() throws Exception {
        WebSocketSession session = openSession("session-A");

        handler.handleMessage(session, new TextMessage("""
            {"type":"HEARTBEAT","workerId":"worker-001","freeVramMb":7600,
             "gpuUtilization":4,"workerStatus":"IDLE"}
            """));

        verify(session).close(CloseStatus.POLICY_VIOLATION);
        assertThat(registry.findAll()).isEmpty();
    }

    @Test
    void replacementBecomesActiveBeforeOldSessionIsClosed() throws Exception {
        WebSocketSession sessionA = openSession("session-A");
        WebSocketSession sessionB = openSession("session-B");
        handler.handleMessage(sessionA, new TextMessage(validRegisterJson()));
        doAnswer(invocation -> {
            assertThat(registry.findById("worker-001").orElseThrow().activeSession())
                .isSameAs(sessionB);
            return null;
        }).when(sessionA).close(
            CloseStatus.POLICY_VIOLATION.withReason("replaced by newer connection")
        );

        handler.handleMessage(sessionB, new TextMessage(validRegisterJson()));

        verify(sessionA).close(
            CloseStatus.POLICY_VIOLATION.withReason("replaced by newer connection")
        );
        assertThat(registry.findById("worker-001").orElseThrow().activeSession())
            .isSameAs(sessionB);
    }

    @Test
    void staleDisconnectKeepsReplacementOnlineAndActiveDisconnectSetsOffline() throws Exception {
        WebSocketSession sessionA = openSession("session-A");
        WebSocketSession sessionB = openSession("session-B");
        handler.handleMessage(sessionA, new TextMessage(validRegisterJson()));
        handler.handleMessage(sessionB, new TextMessage(validRegisterJson()));

        handler.afterConnectionClosed(sessionA, CloseStatus.NORMAL);

        WorkerRecord online = registry.findById("worker-001").orElseThrow();
        assertThat(online.connectionStatus()).isEqualTo(ConnectionStatus.ONLINE);
        assertThat(online.activeSession()).isSameAs(sessionB);

        handler.afterConnectionClosed(sessionB, CloseStatus.NORMAL);

        WorkerRecord offline = registry.findById("worker-001").orElseThrow();
        assertThat(offline.connectionStatus()).isEqualTo(ConnectionStatus.OFFLINE);
        assertThat(offline.workerStatus().name()).isEqualTo("IDLE");
        assertThat(offline.activeSession()).isNull();
    }

    @Test
    void staleSessionHeartbeatCannotChangeReplacement() throws Exception {
        WebSocketSession sessionA = openSession("session-A");
        WebSocketSession sessionB = openSession("session-B");
        handler.handleMessage(sessionA, new TextMessage(validRegisterJson()));
        handler.handleMessage(sessionB, new TextMessage(validRegisterJson()));

        handler.handleMessage(sessionA, new TextMessage("""
            {"type":"HEARTBEAT","workerId":"worker-001","freeVramMb":1,
             "gpuUtilization":100,"workerStatus":"BUSY"}
            """));

        verify(sessionA).close(CloseStatus.POLICY_VIOLATION);
        WorkerRecord current = registry.findById("worker-001").orElseThrow();
        assertThat(current.connectionStatus()).isEqualTo(ConnectionStatus.ONLINE);
        assertThat(current.activeSession()).isSameAs(sessionB);
        assertThat(current.freeVramMb()).isEqualTo(7800);
        assertThat(current.workerStatus().name()).isEqualTo("IDLE");
    }

    @Test
    void heartbeatWorkerIdMismatchClosesWithoutMutation() throws Exception {
        WebSocketSession session = openSession("session-A");
        handler.handleMessage(session, new TextMessage(validRegisterJson()));

        handler.handleMessage(session, new TextMessage("""
            {"type":"HEARTBEAT","workerId":"worker-other","freeVramMb":1,
             "gpuUtilization":100,"workerStatus":"BUSY"}
            """));

        verify(session).close(CloseStatus.POLICY_VIOLATION);
        WorkerRecord current = registry.findById("worker-001").orElseThrow();
        assertThat(current.freeVramMb()).isEqualTo(7800);
        assertThat(current.workerStatus().name()).isEqualTo("IDLE");
    }

    private static WebSocketSession openSession(String id) {
        WebSocketSession session = mock(WebSocketSession.class);
        when(session.getId()).thenReturn(id);
        when(session.isOpen()).thenReturn(true);
        return session;
    }

    private static String validRegisterJson() {
        return """
            {"type":"REGISTER","workerId":"worker-001","gpuModel":"NVIDIA Test GPU",
             "totalVramMb":8188,"freeVramMb":7800,"gpuUtilization":0,
             "cudaAvailable":true,"supportedWorkloads":["EMBEDDING"],
             "hourlyPrice":600,"workerStatus":"IDLE"}
            """;
    }
}
