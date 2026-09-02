package com.gpucompute.poc.backend.protocol;

import java.util.List;
import java.util.stream.Stream;

import tools.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.springframework.web.socket.CloseStatus;

import com.gpucompute.poc.backend.domain.WorkerRegistration;
import com.gpucompute.poc.backend.domain.WorkerStatus;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class RegisterMessageTest {

    private final WorkerMessageDecoder decoder = new WorkerMessageDecoder(new ObjectMapper());
    private final WorkerMessageValidator validator = new WorkerMessageValidator();

    @Test
    void decodesAndValidatesApprovedRegisterContract() {
        String json = """
            {
              "type": "REGISTER",
              "workerId": "worker-001",
              "gpuModel": "NVIDIA GeForce RTX 4070 Laptop GPU",
              "totalVramMb": 8188,
              "freeVramMb": 7800,
              "gpuUtilization": 0,
              "cudaAvailable": true,
              "supportedWorkloads": ["EMBEDDING", "EMBEDDING", "FUTURE_WORKLOAD"],
              "hourlyPrice": 600,
              "workerStatus": "IDLE"
            }
            """;

        RegisterMessage message = (RegisterMessage) decoder.decode(json);
        WorkerRegistration registration = validator.validate(message);

        assertThat(message.type()).isEqualTo(WorkerMessageType.REGISTER);
        assertThat(registration.workerId()).isEqualTo("worker-001");
        assertThat(registration.gpuModel()).isEqualTo("NVIDIA GeForce RTX 4070 Laptop GPU");
        assertThat(registration.totalVramMb()).isEqualTo(8188);
        assertThat(registration.freeVramMb()).isEqualTo(7800);
        assertThat(registration.gpuUtilization()).isZero();
        assertThat(registration.cudaAvailable()).isTrue();
        assertThat(registration.supportedWorkloads())
            .containsExactlyInAnyOrder("EMBEDDING", "FUTURE_WORKLOAD");
        assertThat(registration.hourlyPrice()).isEqualTo(600);
        assertThat(registration.workerStatus()).isEqualTo(WorkerStatus.IDLE);
        assertThat(registration.benchmark()).isNull();
    }

    @Test
    void registerWithBenchmarkValidatesNumericContract() {
        String json = """
            {
              "type": "REGISTER",
              "workerId": "worker-001",
              "gpuModel": "NVIDIA GeForce RTX 4070 Laptop GPU",
              "totalVramMb": 8188,
              "freeVramMb": 7800,
              "gpuUtilization": 0,
              "cudaAvailable": true,
              "supportedWorkloads": ["GPU_PYTORCH_TEST_30S"],
              "hourlyPrice": 600,
              "workerStatus": "IDLE",
              "benchmark": {
                "version": "gpu-benchmark-v1",
                "score": 12.34,
                "throughputTflops": 12.34,
                "matrixSize": 4096,
                "precision": "FP16",
                "measuredAt": "2026-09-01T00:00:00Z"
              }
            }
            """;

        WorkerRegistration registration = validator.validate(
            (RegisterMessage) decoder.decode(json)
        );

        assertThat(registration.benchmark().version()).isEqualTo("gpu-benchmark-v1");
        assertThat(registration.benchmark().score()).isEqualTo(12.34);
        assertThat(registration.benchmark().throughputTflops()).isEqualTo(12.34);
        assertThat(registration.benchmark().measuredAt())
            .isEqualTo(java.time.Instant.parse("2026-09-01T00:00:00Z"));
    }

    @Test
    void rejectsNegativeBenchmarkScore() {
        RegisterMessage message = withBenchmark(new BenchmarkMessage(
            "gpu-benchmark-v1", -1.0, 12.34, 4096, "FP16",
            "2026-09-01T00:00:00Z"
        ));

        assertThatThrownBy(() -> validator.validate(message))
            .isInstanceOf(ProtocolException.class);
    }

    @Test
    void rejectsNonFiniteBenchmarkNumbers() {
        for (double value : new double[] {
            Double.NaN, Double.POSITIVE_INFINITY, Double.NEGATIVE_INFINITY
        }) {
            RegisterMessage message = withBenchmark(new BenchmarkMessage(
                "gpu-benchmark-v1", value, value, 4096, "FP16",
                "2026-09-01T00:00:00Z"
            ));
            assertThatThrownBy(() -> validator.validate(message))
                .isInstanceOf(ProtocolException.class);
        }
    }

    private static RegisterMessage withBenchmark(BenchmarkMessage benchmark) {
        return new RegisterMessage(
            WorkerMessageType.REGISTER, "worker-001", "GPU", 8188L, 7800L,
            0, true, List.of("EMBEDDING"), 600L, "IDLE", benchmark
        );
    }

    @Test
    void malformedJsonUsesBadDataCloseStatus() {
        assertProtocolError("{", CloseStatus.BAD_DATA);
    }

    @Test
    void unknownMessageTypeUsesNotAcceptableCloseStatus() {
        assertProtocolError("{\"type\":\"EXECUTE\"}", CloseStatus.NOT_ACCEPTABLE);
    }

    @Test
    void fractionalIntegerFieldUsesBadDataCloseStatus() {
        String json = """
            {"type":"REGISTER","workerId":"worker-001","gpuModel":"GPU",
             "totalVramMb":8188.5,"freeVramMb":7800,"gpuUtilization":0,
             "cudaAvailable":true,"supportedWorkloads":["EMBEDDING"],
             "hourlyPrice":600,"workerStatus":"IDLE"}
            """;

        assertProtocolError(json, CloseStatus.BAD_DATA);
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("invalidRegistrations")
    void rejectsInvalidRegisterFields(String description, RegisterMessage message) {
        assertThatThrownBy(() -> validator.validate(message))
            .isInstanceOfSatisfying(ProtocolException.class, error ->
                assertThat(error.closeStatus()).isEqualTo(CloseStatus.BAD_DATA));
    }

    private void assertProtocolError(String json, CloseStatus closeStatus) {
        assertThatThrownBy(() -> decoder.decode(json))
            .isInstanceOfSatisfying(ProtocolException.class, error ->
                assertThat(error.closeStatus()).isEqualTo(closeStatus));
    }

    private static Stream<Arguments> invalidRegistrations() {
        return Stream.of(
            Arguments.of("blank workerId", valid().withWorkerId(" ")),
            Arguments.of("blank gpuModel", valid().withGpuModel("")),
            Arguments.of("zero totalVramMb", valid().withTotalVramMb(0L)),
            Arguments.of("negative freeVramMb", valid().withFreeVramMb(-1L)),
            Arguments.of("freeVramMb above total", valid().withFreeVramMb(8189L)),
            Arguments.of("negative utilization", valid().withGpuUtilization(-1)),
            Arguments.of("utilization above 100", valid().withGpuUtilization(101)),
            Arguments.of("missing cudaAvailable", valid().withCudaAvailable(null)),
            Arguments.of("missing workloads", valid().withSupportedWorkloads(null)),
            Arguments.of("empty workloads", valid().withSupportedWorkloads(List.of())),
            Arguments.of("blank workload", valid().withSupportedWorkloads(List.of("EMBEDDING", " "))),
            Arguments.of("missing hourlyPrice", valid().withHourlyPrice(null)),
            Arguments.of("negative hourlyPrice", valid().withHourlyPrice(-1L)),
            Arguments.of("missing workerStatus", valid().withWorkerStatus(null)),
            Arguments.of("unknown workerStatus", valid().withWorkerStatus("ONLINE"))
        );
    }

    private static RegisterMessageBuilder valid() {
        return new RegisterMessageBuilder(
            "worker-001", "GPU", 8188L, 7800L, 0, true,
            List.of("EMBEDDING"), 600L, "IDLE"
        );
    }

    private record RegisterMessageBuilder(
        String workerId,
        String gpuModel,
        Long totalVramMb,
        Long freeVramMb,
        Integer gpuUtilization,
        Boolean cudaAvailable,
        List<String> supportedWorkloads,
        Long hourlyPrice,
        String workerStatus
    ) {
        RegisterMessage build() {
            return new RegisterMessage(
                WorkerMessageType.REGISTER, workerId, gpuModel, totalVramMb,
                freeVramMb, gpuUtilization, cudaAvailable, supportedWorkloads,
                hourlyPrice, workerStatus
            );
        }

        RegisterMessage withWorkerId(String value) { return copy(value, gpuModel, totalVramMb, freeVramMb, gpuUtilization, cudaAvailable, supportedWorkloads, hourlyPrice, workerStatus); }
        RegisterMessage withGpuModel(String value) { return copy(workerId, value, totalVramMb, freeVramMb, gpuUtilization, cudaAvailable, supportedWorkloads, hourlyPrice, workerStatus); }
        RegisterMessage withTotalVramMb(Long value) { return copy(workerId, gpuModel, value, freeVramMb, gpuUtilization, cudaAvailable, supportedWorkloads, hourlyPrice, workerStatus); }
        RegisterMessage withFreeVramMb(Long value) { return copy(workerId, gpuModel, totalVramMb, value, gpuUtilization, cudaAvailable, supportedWorkloads, hourlyPrice, workerStatus); }
        RegisterMessage withGpuUtilization(Integer value) { return copy(workerId, gpuModel, totalVramMb, freeVramMb, value, cudaAvailable, supportedWorkloads, hourlyPrice, workerStatus); }
        RegisterMessage withCudaAvailable(Boolean value) { return copy(workerId, gpuModel, totalVramMb, freeVramMb, gpuUtilization, value, supportedWorkloads, hourlyPrice, workerStatus); }
        RegisterMessage withSupportedWorkloads(List<String> value) { return copy(workerId, gpuModel, totalVramMb, freeVramMb, gpuUtilization, cudaAvailable, value, hourlyPrice, workerStatus); }
        RegisterMessage withHourlyPrice(Long value) { return copy(workerId, gpuModel, totalVramMb, freeVramMb, gpuUtilization, cudaAvailable, supportedWorkloads, value, workerStatus); }
        RegisterMessage withWorkerStatus(String value) { return copy(workerId, gpuModel, totalVramMb, freeVramMb, gpuUtilization, cudaAvailable, supportedWorkloads, hourlyPrice, value); }

        private RegisterMessage copy(
            String newWorkerId, String newGpuModel, Long newTotalVramMb,
            Long newFreeVramMb, Integer newGpuUtilization, Boolean newCudaAvailable,
            List<String> newSupportedWorkloads, Long newHourlyPrice, String newWorkerStatus
        ) {
            return new RegisterMessage(
                WorkerMessageType.REGISTER, newWorkerId, newGpuModel,
                newTotalVramMb, newFreeVramMb, newGpuUtilization,
                newCudaAvailable, newSupportedWorkloads, newHourlyPrice,
                newWorkerStatus
            );
        }
    }
}
