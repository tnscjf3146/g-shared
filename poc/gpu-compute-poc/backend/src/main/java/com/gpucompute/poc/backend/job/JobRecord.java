package com.gpucompute.poc.backend.job;

import java.time.Instant;
import java.util.UUID;

import com.gpucompute.poc.backend.domain.JobStatus;

public record JobRecord(
    UUID jobId,
    String workerId,
    String workload,
    JobStatus status,
    Instant createdAt,
    Instant completedAt,
    String gpuModel,
    Long runtimeMs,
    String resultChecksum,
    Integer containerExitCode,
    Boolean imageCacheHit,
    String errorCode,
    String errorMessage,
    String containerId,
    String containerName,
    String imageDigest,
    Integer iterations,
    Integer matrixSize,
    Integer targetDurationSeconds
) {
    public JobRecord(
        UUID jobId,
        String workerId,
        String workload,
        JobStatus status,
        Instant createdAt,
        Instant completedAt,
        String gpuModel,
        Long runtimeMs,
        String resultChecksum,
        Integer containerExitCode,
        Boolean imageCacheHit,
        String errorCode,
        String errorMessage
    ) {
        this(jobId, workerId, workload, status, createdAt, completedAt, gpuModel,
            runtimeMs, resultChecksum, containerExitCode, imageCacheHit, errorCode,
            errorMessage, null, null, null, null, null, null);
    }

    public JobRecord started(
        Instant at,
        String startedContainerId,
        String startedContainerName,
        String startedImageDigest
    ) {
        return new JobRecord(jobId, workerId, workload, JobStatus.RUNNING, createdAt,
            null, null, null, null, null, null, null, null, startedContainerId,
            startedContainerName, startedImageDigest, null, null, null);
    }

    public JobRecord complete(
        JobStatus terminalStatus,
        Instant at,
        String gpu,
        Long runtime,
        String checksum,
        Integer exitCode,
        Boolean cacheHit,
        String code,
        String message
    ) {
        return complete(terminalStatus, at, gpu, runtime, checksum, exitCode, cacheHit,
            code, message, containerId, containerName, imageDigest, iterations,
            matrixSize, targetDurationSeconds);
    }

    public JobRecord complete(
        JobStatus terminalStatus,
        Instant at,
        String gpu,
        Long runtime,
        String checksum,
        Integer exitCode,
        Boolean cacheHit,
        String code,
        String message,
        String completedContainerId,
        String completedContainerName,
        String completedImageDigest,
        Integer completedIterations,
        Integer completedMatrixSize,
        Integer completedTargetDurationSeconds
    ) {
        return new JobRecord(jobId, workerId, workload, terminalStatus, createdAt, at,
            gpu, runtime, checksum, exitCode, cacheHit, code, message,
            completedContainerId, completedContainerName, completedImageDigest,
            completedIterations, completedMatrixSize, completedTargetDurationSeconds);
    }
}
