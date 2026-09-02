package com.gpucompute.poc.backend.api;

import java.time.Instant;
import java.util.Set;

import com.gpucompute.poc.backend.domain.ConnectionStatus;
import com.gpucompute.poc.backend.domain.WorkerStatus;
import com.gpucompute.poc.backend.registry.WorkerRecord;

public record WorkerResponse(
    String workerId,
    String gpuModel,
    long totalVramMb,
    long freeVramMb,
    int gpuUtilization,
    boolean cudaAvailable,
    Set<String> supportedWorkloads,
    long hourlyPrice,
    ConnectionStatus connectionStatus,
    WorkerStatus workerStatus,
    BenchmarkResponse benchmark,
    Instant lastHeartbeat
) {
    static WorkerResponse from(WorkerRecord record) {
        return new WorkerResponse(
            record.workerId(),
            record.gpuModel(),
            record.totalVramMb(),
            record.freeVramMb(),
            record.gpuUtilization(),
            record.cudaAvailable(),
            record.supportedWorkloads(),
            record.hourlyPrice(),
            record.connectionStatus(),
            record.workerStatus(),
            BenchmarkResponse.from(record.benchmark()),
            record.lastHeartbeat()
        );
    }
}
