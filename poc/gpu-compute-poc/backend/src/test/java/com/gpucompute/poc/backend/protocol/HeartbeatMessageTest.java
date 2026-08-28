package com.gpucompute.poc.backend.protocol;

import java.util.stream.Stream;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.springframework.web.socket.CloseStatus;
import tools.jackson.databind.ObjectMapper;

import com.gpucompute.poc.backend.domain.WorkerHeartbeat;
import com.gpucompute.poc.backend.domain.WorkerStatus;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class HeartbeatMessageTest {

    private final WorkerMessageDecoder decoder = new WorkerMessageDecoder(new ObjectMapper());
    private final WorkerMessageValidator validator = new WorkerMessageValidator();

    @Test
    void decodesAndValidatesHeartbeatContract() {
        HeartbeatMessage message = (HeartbeatMessage) decoder.decode("""
            {"type":"HEARTBEAT","workerId":"worker-001","freeVramMb":7650,
             "gpuUtilization":4,"workerStatus":"BUSY"}
            """);

        WorkerHeartbeat heartbeat = validator.validate(message, 8188);

        assertThat(heartbeat.workerId()).isEqualTo("worker-001");
        assertThat(heartbeat.freeVramMb()).isEqualTo(7650);
        assertThat(heartbeat.gpuUtilization()).isEqualTo(4);
        assertThat(heartbeat.workerStatus()).isEqualTo(WorkerStatus.BUSY);
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("invalidHeartbeats")
    void rejectsInvalidHeartbeatFields(String description, HeartbeatMessage message) {
        assertThatThrownBy(() -> validator.validate(message, 8188))
            .isInstanceOfSatisfying(ProtocolException.class, error ->
                assertThat(error.closeStatus()).isEqualTo(CloseStatus.BAD_DATA));
    }

    private static Stream<Arguments> invalidHeartbeats() {
        return Stream.of(
            Arguments.of("blank workerId", heartbeat(" ", 100L, 0, "IDLE")),
            Arguments.of("missing freeVramMb", heartbeat("worker-001", null, 0, "IDLE")),
            Arguments.of("negative freeVramMb", heartbeat("worker-001", -1L, 0, "IDLE")),
            Arguments.of("freeVramMb above registered total", heartbeat("worker-001", 8189L, 0, "IDLE")),
            Arguments.of("missing utilization", heartbeat("worker-001", 100L, null, "IDLE")),
            Arguments.of("negative utilization", heartbeat("worker-001", 100L, -1, "IDLE")),
            Arguments.of("utilization above 100", heartbeat("worker-001", 100L, 101, "IDLE")),
            Arguments.of("missing workerStatus", heartbeat("worker-001", 100L, 0, null)),
            Arguments.of("invalid workerStatus", heartbeat("worker-001", 100L, 0, "ONLINE"))
        );
    }

    private static HeartbeatMessage heartbeat(
        String workerId,
        Long freeVramMb,
        Integer gpuUtilization,
        String workerStatus
    ) {
        return new HeartbeatMessage(
            WorkerMessageType.HEARTBEAT,
            workerId,
            freeVramMb,
            gpuUtilization,
            workerStatus
        );
    }
}
