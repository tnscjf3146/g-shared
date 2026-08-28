package com.gpucompute.poc.backend.protocol;

import java.util.List;

public record RegisterMessage(
    WorkerMessageType type,
    String workerId,
    String gpuModel,
    Long totalVramMb,
    Long freeVramMb,
    Integer gpuUtilization,
    Boolean cudaAvailable,
    List<String> supportedWorkloads,
    Long hourlyPrice,
    String workerStatus
) implements WorkerMessage {
}
