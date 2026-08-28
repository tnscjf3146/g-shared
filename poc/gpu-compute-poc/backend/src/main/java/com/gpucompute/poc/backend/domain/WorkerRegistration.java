package com.gpucompute.poc.backend.domain;

import java.util.Objects;
import java.util.Set;

public record WorkerRegistration(
    String workerId,
    String gpuModel,
    long totalVramMb,
    long freeVramMb,
    int gpuUtilization,
    boolean cudaAvailable,
    Set<String> supportedWorkloads,
    long hourlyPrice,
    WorkerStatus workerStatus
) {
    public WorkerRegistration {
        Objects.requireNonNull(workerId, "workerId");
        Objects.requireNonNull(gpuModel, "gpuModel");
        supportedWorkloads = Set.copyOf(supportedWorkloads);
        Objects.requireNonNull(workerStatus, "workerStatus");
    }
}
