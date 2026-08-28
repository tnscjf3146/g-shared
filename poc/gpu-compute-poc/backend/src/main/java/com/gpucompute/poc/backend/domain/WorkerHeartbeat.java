package com.gpucompute.poc.backend.domain;

import java.util.Objects;

public record WorkerHeartbeat(
    String workerId,
    long freeVramMb,
    int gpuUtilization,
    WorkerStatus workerStatus
) {
    public WorkerHeartbeat {
        Objects.requireNonNull(workerId, "workerId");
        Objects.requireNonNull(workerStatus, "workerStatus");
    }
}
