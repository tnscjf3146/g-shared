package com.gshared.back.job.realtime;

import com.gshared.back.job.controller.JobController;
import com.gshared.back.worker.websocket.protocol.WorkerMessageType;
import org.junit.jupiter.api.Test;
import org.springframework.web.bind.annotation.GetMapping;

import java.util.Arrays;

import static org.assertj.core.api.Assertions.assertThat;

class JobRealtimeCompatibilityTest {
    @Test
    void pollingJobDetailEndpointRemainsAvailable() throws Exception {
        GetMapping mapping = JobController.class
                .getMethod("getJob", Long.class,
                        com.gshared.back.global.security.userdetails.CustomUserDetails.class)
                .getAnnotation(GetMapping.class);

        assertThat(mapping.value()).containsExactly("/{jobId}");
    }

    @Test
    void workerWebSocketMessageContractIsUnchanged() {
        assertThat(Arrays.stream(WorkerMessageType.values()).map(Enum::name))
                .contains("JOB_PROGRESS", "JOB_RESULT", "JOB_FAILED", "JOB_CANCELLED");
    }
}
