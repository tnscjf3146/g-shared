package com.gpucompute.poc.backend.protocol;

import java.util.UUID;

public record ExecuteResultMessage(
    WorkerMessageType type,
    UUID jobId,
    ExecuteResultStatus status,
    String gpuModel,
    Long runtimeMs,
    String resultChecksum,
    Integer containerExitCode,
    Boolean imageCacheHit,
    String errorCode,
    String errorMessage,
    String containerId,
    String containerName,
    String workload,
    String imageDigest,
    Integer iterations,
    Integer matrixSize,
    Integer targetDurationSeconds
) implements WorkerMessage {}
