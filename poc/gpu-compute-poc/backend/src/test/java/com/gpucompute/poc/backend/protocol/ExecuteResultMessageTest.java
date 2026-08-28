package com.gpucompute.poc.backend.protocol;

import org.junit.jupiter.api.Test;
import tools.jackson.databind.ObjectMapper;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class ExecuteResultMessageTest {
    private final WorkerMessageDecoder decoder = new WorkerMessageDecoder(new ObjectMapper());

    @Test
    void decodesSuccessfulResult() {
        WorkerMessage message = decoder.decode("""
            {"type":"EXECUTE_RESULT","jobId":"f271e9f5-0d9f-4fbe-bac1-8812647a02ba",
             "status":"SUCCESS","gpuModel":"NVIDIA Test GPU","runtimeMs":850,
             "resultChecksum":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
             "containerExitCode":0,"imageCacheHit":true}
            """);

        assertThat(message).isInstanceOf(ExecuteResultMessage.class);
    }

    @Test
    void rejectsBackendControlledExecutionFields() {
        assertThatThrownBy(() -> decoder.decode("""
            {"type":"EXECUTE_RESULT","jobId":"f271e9f5-0d9f-4fbe-bac1-8812647a02ba",
             "status":"SUCCESS","image":"evil/latest"}
            """))
            .isInstanceOf(ProtocolException.class);
    }

    @Test
    void decodesTimedResultWithExecutionProof() {
        WorkerMessage message = decoder.decode("""
            {"type":"EXECUTE_RESULT","jobId":"f271e9f5-0d9f-4fbe-bac1-8812647a02ba",
             "status":"SUCCESS","gpuModel":"NVIDIA Test GPU","runtimeMs":30010,
             "resultChecksum":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
             "containerExitCode":0,"imageCacheHit":true,"containerId":"abc123",
             "containerName":"gpu-poc-job-test","workload":"GPU_PYTORCH_TEST_30S",
             "imageDigest":"le3560/image@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
             "iterations":123,"matrixSize":8192,"targetDurationSeconds":30}
            """);

        ExecuteResultMessage result = (ExecuteResultMessage) message;
        assertThat(result.iterations()).isEqualTo(123);
        assertThat(result.containerId()).isEqualTo("abc123");
    }
}
