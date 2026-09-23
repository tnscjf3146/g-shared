package com.gshared.back.job.service;

import com.gshared.back.global.client.dto.SchedulerRecommendRequest;
import org.junit.jupiter.api.Test;

import java.util.Arrays;

import static org.assertj.core.api.Assertions.assertThat;

class WorkerReliabilitySchedulerBoundaryTest {
    @Test
    void schedulerGpuCandidateContractDoesNotContainReliability() {
        assertThat(Arrays.stream(SchedulerRecommendRequest.GpuCandidate.class.getRecordComponents())
                .map(component -> component.getName()))
                .containsExactly("workerId", "gpuModel", "vramMb", "benchmarkTokensPerSec",
                        "benchmarkPeakVramMb");
    }
}
