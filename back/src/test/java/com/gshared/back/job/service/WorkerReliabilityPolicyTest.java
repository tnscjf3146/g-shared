package com.gshared.back.job.service;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class WorkerReliabilityPolicyTest {
    @Test
    void runtimeAndGpuFailuresCountAgainstReliability() {
        assertThat(WorkerReliabilityPolicy.isWorkerFailure("RUNTIME_ERROR")).isTrue();
        assertThat(WorkerReliabilityPolicy.isWorkerFailure("CUDA_OOM")).isTrue();
        assertThat(WorkerReliabilityPolicy.isWorkerFailure("CONTAINER_FAILED")).isTrue();
        assertThat(WorkerReliabilityPolicy.isWorkerFailure("WORKER_DISCONNECTED")).isTrue();
    }

    @Test
    void userEscrowAndPreAcceptFailuresDoNotCount() {
        assertThat(WorkerReliabilityPolicy.isWorkerFailure("USER_REQUEST")).isFalse();
        assertThat(WorkerReliabilityPolicy.isWorkerFailure("ESCROW_CANCELLED")).isFalse();
        assertThat(WorkerReliabilityPolicy.isWorkerFailure("BLOCKCHAIN_RPC_FAILED")).isFalse();
        assertThat(WorkerReliabilityPolicy.isWorkerFailure("PRICE_MISMATCH")).isFalse();
        assertThat(WorkerReliabilityPolicy.isWorkerFailure(null)).isFalse();
    }
}
