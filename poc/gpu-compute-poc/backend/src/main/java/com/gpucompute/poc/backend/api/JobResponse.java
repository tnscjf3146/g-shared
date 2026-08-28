package com.gpucompute.poc.backend.api;

import java.time.Instant;
import java.util.UUID;

import com.gpucompute.poc.backend.domain.JobStatus;
import com.gpucompute.poc.backend.job.JobRecord;

public record JobResponse(
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
    static JobResponse from(JobRecord job) {
        return new JobResponse(job.jobId(), job.workerId(), job.workload(), job.status(),
            job.createdAt(), job.completedAt(), job.gpuModel(), job.runtimeMs(),
            job.resultChecksum(), job.containerExitCode(), job.imageCacheHit(),
            job.errorCode(), job.errorMessage(), job.containerId(), job.containerName(),
            job.imageDigest(), job.iterations(), job.matrixSize(),
            job.targetDurationSeconds());
    }
}
