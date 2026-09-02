package com.gpucompute.poc.backend.config;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class ApprovedWorkloadPolicyTest {

    @Test
    void internalBenchmarkCannotBeApprovedForJobApiEvenIfMisconfigured() {
        ApprovedWorkloadPolicy policy = new ApprovedWorkloadPolicy(
            "GPU_PYTORCH_TEST,GPU_STANDARD_BENCHMARK"
        );

        assertThat(policy.isApproved("GPU_PYTORCH_TEST")).isTrue();
        assertThat(policy.isApproved("GPU_STANDARD_BENCHMARK")).isFalse();
    }
}
