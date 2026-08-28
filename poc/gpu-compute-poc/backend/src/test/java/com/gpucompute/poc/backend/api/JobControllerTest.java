package com.gpucompute.poc.backend.api;

import java.time.Instant;
import java.util.UUID;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

import com.gpucompute.poc.backend.domain.JobStatus;
import com.gpucompute.poc.backend.job.JobRecord;
import com.gpucompute.poc.backend.job.JobService;

import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;
import static org.springframework.test.web.servlet.setup.MockMvcBuilders.standaloneSetup;

class JobControllerTest {
    private static final UUID JOB_ID = UUID.fromString(
        "f271e9f5-0d9f-4fbe-bac1-8812647a02ba"
    );
    private JobService jobs;
    private MockMvc mockMvc;

    @BeforeEach
    void setUp() {
        jobs = mock(JobService.class);
        mockMvc = standaloneSetup(new JobController(jobs)).build();
    }

    @Test
    void postDispatchesExplicitWorkerAndWorkload() throws Exception {
        when(jobs.dispatch("worker-001", "GPU_PYTORCH_TEST"))
            .thenReturn(dispatched());

        mockMvc.perform(post("/api/jobs")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                    {"workerId":"worker-001","workload":"GPU_PYTORCH_TEST"}
                    """))
            .andExpect(status().isAccepted())
            .andExpect(jsonPath("$.jobId").value(JOB_ID.toString()))
            .andExpect(jsonPath("$.status").value("DISPATCHED"))
            .andExpect(jsonPath("$.image").doesNotExist())
            .andExpect(jsonPath("$.command").doesNotExist());
    }

    @Test
    void getReturnsStoredCudaResult() throws Exception {
        JobRecord completed = dispatched().complete(
            JobStatus.SUCCESS, Instant.EPOCH.plusSeconds(2), "NVIDIA Test GPU",
            850L, "a".repeat(64), 0, true, null, null
        );
        when(jobs.findById(JOB_ID)).thenReturn(completed);

        mockMvc.perform(get("/api/jobs/{jobId}", JOB_ID))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.status").value("SUCCESS"))
            .andExpect(jsonPath("$.runtimeMs").value(850))
            .andExpect(jsonPath("$.resultChecksum").value("a".repeat(64)))
            .andExpect(jsonPath("$.imageCacheHit").value(true));
    }

    @Test
    void getReturnsExecutionProofForTimedJob() throws Exception {
        JobRecord running = dispatched().started(
            Instant.EPOCH.plusSeconds(1), "cid", "gpu-poc-job-" + JOB_ID,
            "le3560/image@sha256:" + "b".repeat(64)
        );
        JobRecord completed = running.complete(
            JobStatus.SUCCESS, Instant.EPOCH.plusSeconds(32), "NVIDIA Test GPU",
            30010L, "a".repeat(64), 0, true, null, null,
            "cid", "gpu-poc-job-" + JOB_ID,
            "le3560/image@sha256:" + "b".repeat(64), 123, 8192, 30
        );
        when(jobs.findById(JOB_ID)).thenReturn(completed);

        mockMvc.perform(get("/api/jobs/{jobId}", JOB_ID))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.containerId").value("cid"))
            .andExpect(jsonPath("$.containerName").value("gpu-poc-job-" + JOB_ID))
            .andExpect(jsonPath("$.iterations").value(123))
            .andExpect(jsonPath("$.matrixSize").value(8192))
            .andExpect(jsonPath("$.targetDurationSeconds").value(30));
    }

    private static JobRecord dispatched() {
        return new JobRecord(JOB_ID, "worker-001", "GPU_PYTORCH_TEST",
            JobStatus.DISPATCHED, Instant.EPOCH, null, null, null, null,
            null, null, null, null);
    }
}
