package com.gpucompute.poc.backend.api;

import java.time.Instant;
import java.util.Set;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.web.socket.WebSocketSession;

import com.gpucompute.poc.backend.domain.WorkerRegistration;
import com.gpucompute.poc.backend.domain.WorkerStatus;
import com.gpucompute.poc.backend.registry.WorkerRegistry;

import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;
import static org.springframework.test.web.servlet.setup.MockMvcBuilders.standaloneSetup;

class WorkerControllerTest {

    private WorkerRegistry registry;
    private MockMvc mockMvc;

    @BeforeEach
    void setUp() {
        registry = new WorkerRegistry();
        mockMvc = standaloneSetup(new WorkerController(registry)).build();
    }

    @Test
    void listReturnsWorkersSortedByIdWithoutSessions() throws Exception {
        register("worker-002", "session-B", WorkerStatus.BUSY);
        register("worker-001", "session-A", WorkerStatus.IDLE);

        mockMvc.perform(get("/api/workers"))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$[0].workerId").value("worker-001"))
            .andExpect(jsonPath("$[1].workerId").value("worker-002"))
            .andExpect(jsonPath("$[0].session").doesNotExist())
            .andExpect(jsonPath("$[0].activeSession").doesNotExist());
    }

    @Test
    void detailReturnsCompletePublicWorkerState() throws Exception {
        register("worker-001", "session-A", WorkerStatus.IDLE);

        mockMvc.perform(get("/api/workers/worker-001"))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.workerId").value("worker-001"))
            .andExpect(jsonPath("$.gpuModel").value("NVIDIA Test GPU"))
            .andExpect(jsonPath("$.totalVramMb").value(8188))
            .andExpect(jsonPath("$.freeVramMb").value(7800))
            .andExpect(jsonPath("$.gpuUtilization").value(4))
            .andExpect(jsonPath("$.cudaAvailable").value(true))
            .andExpect(jsonPath("$.supportedWorkloads[0]").value("EMBEDDING"))
            .andExpect(jsonPath("$.hourlyPrice").value(600))
            .andExpect(jsonPath("$.connectionStatus").value("ONLINE"))
            .andExpect(jsonPath("$.workerStatus").value("IDLE"))
            .andExpect(jsonPath("$.lastHeartbeat").value("2026-08-27T00:00:00Z"))
            .andExpect(jsonPath("$.session").doesNotExist())
            .andExpect(jsonPath("$.activeSession").doesNotExist());
    }

    @Test
    void offlineDetailPreservesLastWorkerStatus() throws Exception {
        register("worker-001", "session-A", WorkerStatus.BUSY);
        registry.disconnectIfActive("worker-001", "session-A");

        mockMvc.perform(get("/api/workers/worker-001"))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.connectionStatus").value("OFFLINE"))
            .andExpect(jsonPath("$.workerStatus").value("BUSY"));
    }

    @Test
    void unknownWorkerReturnsNotFound() throws Exception {
        mockMvc.perform(get("/api/workers/missing"))
            .andExpect(status().isNotFound());
    }

    private void register(String workerId, String sessionId, WorkerStatus workerStatus) {
        WebSocketSession session = mock(WebSocketSession.class);
        when(session.getId()).thenReturn(sessionId);
        registry.register(
            new WorkerRegistration(
                workerId,
                "NVIDIA Test GPU",
                8188,
                7800,
                4,
                true,
                Set.of("EMBEDDING"),
                600,
                workerStatus
            ),
            session,
            Instant.parse("2026-08-27T00:00:00Z")
        );
    }
}
