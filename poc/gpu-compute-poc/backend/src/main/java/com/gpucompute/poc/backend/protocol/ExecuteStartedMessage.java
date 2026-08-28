package com.gpucompute.poc.backend.protocol;

import java.util.UUID;

public record ExecuteStartedMessage(
    WorkerMessageType type,
    UUID jobId,
    String workerId,
    String containerId,
    String containerName,
    String workload,
    String imageDigest
) implements WorkerMessage {}
