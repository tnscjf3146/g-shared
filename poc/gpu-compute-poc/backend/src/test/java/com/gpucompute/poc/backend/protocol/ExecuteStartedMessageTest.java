package com.gpucompute.poc.backend.protocol;

import org.junit.jupiter.api.Test;
import tools.jackson.databind.ObjectMapper;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class ExecuteStartedMessageTest {
    private final WorkerMessageDecoder decoder = new WorkerMessageDecoder(new ObjectMapper());

    @Test
    void decodesWorkerGeneratedContainerProof() {
        WorkerMessage message = decoder.decode("""
            {"type":"EXECUTE_STARTED",
             "jobId":"f271e9f5-0d9f-4fbe-bac1-8812647a02ba",
             "workerId":"worker-001","containerId":"abc123",
             "containerName":"gpu-poc-job-f271e9f5-0d9f-4fbe-bac1-8812647a02ba",
             "workload":"GPU_PYTORCH_TEST_30S",
             "imageDigest":"le3560/gpu-pytorch-poc-30s@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
            """);

        assertThat(message).isInstanceOf(ExecuteStartedMessage.class);
    }

    @Test
    void rejectsArbitraryDockerOptions() {
        assertThatThrownBy(() -> decoder.decode("""
            {"type":"EXECUTE_STARTED",
             "jobId":"f271e9f5-0d9f-4fbe-bac1-8812647a02ba",
             "workerId":"worker-001","containerId":"abc123",
             "containerName":"gpu-poc-job-test","workload":"GPU_PYTORCH_TEST_30S",
             "imageDigest":"repo/image@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
             "command":"sh"}
            """))
            .isInstanceOf(ProtocolException.class);
    }
}
