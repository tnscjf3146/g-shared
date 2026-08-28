package com.gpucompute.poc.backend.protocol;

import java.util.UUID;

public record ExecuteMessage(String type, UUID jobId, String workload) {
    public ExecuteMessage(UUID jobId, String workload) {
        this("EXECUTE", jobId, workload);
    }
}
