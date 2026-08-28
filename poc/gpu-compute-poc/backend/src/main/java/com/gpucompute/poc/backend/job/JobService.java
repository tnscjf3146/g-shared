package com.gpucompute.poc.backend.job;

import java.io.IOException;
import java.time.Clock;
import java.time.Instant;
import java.util.UUID;

import org.springframework.stereotype.Service;

import com.gpucompute.poc.backend.config.ApprovedWorkloadPolicy;
import com.gpucompute.poc.backend.domain.JobStatus;
import com.gpucompute.poc.backend.protocol.ExecuteMessage;
import com.gpucompute.poc.backend.protocol.ExecuteResultMessage;
import com.gpucompute.poc.backend.protocol.ExecuteResultStatus;
import com.gpucompute.poc.backend.protocol.ExecuteStartedMessage;
import com.gpucompute.poc.backend.registry.JobCompletionResult;
import com.gpucompute.poc.backend.registry.JobProgressResult;
import com.gpucompute.poc.backend.registry.JobReservationResult;
import com.gpucompute.poc.backend.registry.WorkerRegistry;
import com.gpucompute.poc.backend.registry.WorkerDisconnectResult;
import com.gpucompute.poc.backend.websocket.WorkerMessageSender;

@Service
public class JobService {
    private final WorkerRegistry workers;
    private final InMemoryJobRegistry jobs;
    private final ApprovedWorkloadPolicy policy;
    private final WorkerMessageSender sender;
    private final Clock clock;

    public JobService(
        WorkerRegistry workers,
        InMemoryJobRegistry jobs,
        ApprovedWorkloadPolicy policy,
        WorkerMessageSender sender,
        Clock clock
    ) {
        this.workers = workers;
        this.jobs = jobs;
        this.policy = policy;
        this.sender = sender;
        this.clock = clock;
    }

    public JobRecord dispatch(String workerId, String workload) {
        if (workerId == null || workerId.isBlank() || workload == null || workload.isBlank()) {
            throw new IllegalArgumentException("workerId and workload are required");
        }
        if (!policy.isApproved(workload)) {
            throw new JobConflictException("workload is not approved");
        }
        UUID jobId = UUID.randomUUID();
        JobReservationResult reservation = workers.reserveJob(workerId, jobId, workload);
        if (reservation.status() == JobReservationResult.Status.UNKNOWN_WORKER) {
            throw new JobNotFoundException("worker not found");
        }
        if (reservation.status() != JobReservationResult.Status.RESERVED) {
            throw new JobConflictException("worker cannot accept job: " + reservation.status());
        }

        var session = reservation.activeSession().orElseThrow();
        JobRecord job = new JobRecord(jobId, workerId, workload, JobStatus.DISPATCHED,
            clock.instant(), null, null, null, null, null, null, null, null);
        jobs.save(job);
        try {
            sender.send(session, new ExecuteMessage(jobId, workload));
            return job;
        } catch (IOException | RuntimeException error) {
            workers.releaseReservation(workerId, jobId, session.getId());
            JobRecord failed = job.complete(JobStatus.RESULT_UNKNOWN, clock.instant(),
                null, null, null, null, null, "RESULT_UNKNOWN", "EXECUTE send failed");
            jobs.save(failed);
            throw new JobConflictException("failed to send EXECUTE");
        }
    }

    public JobRecord findById(UUID jobId) {
        return jobs.findById(jobId)
            .orElseThrow(() -> new JobNotFoundException("job not found"));
    }

    public JobCompletionResult complete(
        String workerId,
        String sessionId,
        ExecuteResultMessage result
    ) {
        JobRecord currentJob = jobs.findById(result.jobId()).orElse(null);
        if (currentJob == null || !validResultForJob(currentJob, result)) {
            return JobCompletionResult.STALE_JOB;
        }
        JobCompletionResult completion = workers.completeJob(workerId, result.jobId(), sessionId);
        if (completion != JobCompletionResult.APPLIED) {
            return completion;
        }
        JobStatus status = result.status() == ExecuteResultStatus.SUCCESS
            ? JobStatus.SUCCESS : JobStatus.FAILED;
        jobs.update(result.jobId(), current -> current.complete(status, clock.instant(),
            result.gpuModel(), result.runtimeMs(), result.resultChecksum(),
            result.containerExitCode(), result.imageCacheHit(), result.errorCode(),
            result.errorMessage(), result.containerId(), result.containerName(),
            result.imageDigest(), result.iterations(), result.matrixSize(),
            result.targetDurationSeconds()));
        return completion;
    }

    public JobProgressResult started(
        String workerId,
        String sessionId,
        ExecuteStartedMessage message
    ) {
        if (!workerId.equals(message.workerId())) {
            return JobProgressResult.STALE_SESSION;
        }
        JobRecord job = jobs.findById(message.jobId()).orElse(null);
        if (job == null || !job.workerId().equals(workerId)
            || !job.workload().equals(message.workload())
            || job.status() != JobStatus.DISPATCHED) {
            return JobProgressResult.STALE_JOB;
        }
        JobProgressResult progress = workers.validateJobProgress(
            workerId,
            message.jobId(),
            sessionId
        );
        if (progress != JobProgressResult.APPLIED) {
            return progress;
        }
        jobs.update(message.jobId(), current -> current.status() == JobStatus.DISPATCHED
            ? current.started(clock.instant(), message.containerId(),
                message.containerName(), message.imageDigest())
            : current);
        return progress;
    }

    public boolean workerDisconnected(String workerId, String sessionId) {
        WorkerDisconnectResult disconnected = workers.disconnectActive(workerId, sessionId);
        if (disconnected.disconnected() && disconnected.activeJobId() != null) {
            markResultUnknown(disconnected.activeJobId(), "Worker connection closed");
        }
        return disconnected.disconnected();
    }

    public void markResultUnknown(UUID jobId, String reason) {
        jobs.update(jobId, current -> {
            if (current.status() != JobStatus.DISPATCHED
                && current.status() != JobStatus.RUNNING) {
                return current;
            }
            return current.complete(JobStatus.RESULT_UNKNOWN, clock.instant(), null,
                null, null, null, null, "RESULT_UNKNOWN", reason);
        });
    }

    private static boolean validResultForJob(
        JobRecord job,
        ExecuteResultMessage result
    ) {
        if (result.workload() != null && !job.workload().equals(result.workload())) {
            return false;
        }
        if (!"GPU_PYTORCH_TEST_30S".equals(job.workload())) {
            return true;
        }
        if (result.status() == ExecuteResultStatus.FAILED) {
            return job.status() == JobStatus.DISPATCHED || job.status() == JobStatus.RUNNING;
        }
        return job.status() == JobStatus.RUNNING
            && job.containerId() != null
            && job.containerId().equals(result.containerId())
            && job.containerName().equals(result.containerName())
            && job.imageDigest().equals(result.imageDigest())
            && job.workload().equals(result.workload())
            && result.runtimeMs() != null
            && result.runtimeMs() >= 28_000
            && result.runtimeMs() <= 35_000
            && result.iterations() != null
            && result.iterations() > 0
            && result.matrixSize() != null
            && result.matrixSize() >= 2048
            && Integer.valueOf(30).equals(result.targetDurationSeconds());
    }
}
