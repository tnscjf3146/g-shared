package com.gpucompute.poc.backend.protocol;

public record HeartbeatMessage(
    WorkerMessageType type,
    String workerId,
    Long freeVramMb,
    Integer gpuUtilization,
    String workerStatus
) implements WorkerMessage {
}
