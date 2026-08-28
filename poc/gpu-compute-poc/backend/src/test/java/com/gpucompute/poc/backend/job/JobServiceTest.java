package com.gpucompute.poc.backend.job;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.Set;

import org.junit.jupiter.api.Test;
import org.springframework.web.socket.WebSocketSession;

import com.gpucompute.poc.backend.config.ApprovedWorkloadPolicy;
import com.gpucompute.poc.backend.domain.WorkerRegistration;
import com.gpucompute.poc.backend.domain.WorkerStatus;
import com.gpucompute.poc.backend.domain.JobStatus;
import com.gpucompute.poc.backend.protocol.ExecuteStartedMessage;
import com.gpucompute.poc.backend.protocol.ExecuteResultMessage;
import com.gpucompute.poc.backend.protocol.ExecuteResultStatus;
import com.gpucompute.poc.backend.protocol.WorkerMessageType;
import com.gpucompute.poc.backend.registry.JobProgressResult;
import com.gpucompute.poc.backend.registry.WorkerRegistry;
import com.gpucompute.poc.backend.websocket.WorkerMessageSender;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.argThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class JobServiceTest {
    @Test
    void dispatchReservesWorkerAndSendsOnlyJobIdAndWorkload() throws Exception {
        WorkerRegistry workers = new WorkerRegistry();
        WebSocketSession session = mock(WebSocketSession.class);
        when(session.getId()).thenReturn("session-A");
        workers.register(registration(), session, Instant.EPOCH);
        WorkerMessageSender sender = mock(WorkerMessageSender.class);
        InMemoryJobRegistry jobs = new InMemoryJobRegistry();
        JobService service = new JobService(
            workers,
            jobs,
            new ApprovedWorkloadPolicy("GPU_PYTORCH_TEST"),
            sender,
            Clock.fixed(Instant.EPOCH, ZoneOffset.UTC)
        );

        JobRecord job = service.dispatch("worker-001", "GPU_PYTORCH_TEST");

        assertThat(job.status()).isEqualTo(JobStatus.DISPATCHED);
        verify(sender).send(argThat(s -> s == session), argThat(message ->
            message.workload().equals("GPU_PYTORCH_TEST")
                && message.jobId().equals(job.jobId())
                && message.type().equals("EXECUTE")
        ));
        assertThat(workers.findById("worker-001").orElseThrow().workerStatus())
            .isEqualTo(WorkerStatus.BUSY);
    }

    @Test
    void busyWorkerRejectsDuplicateExecute() {
        WorkerRegistry workers = new WorkerRegistry();
        WebSocketSession session = mock(WebSocketSession.class);
        when(session.getId()).thenReturn("session-A");
        workers.register(registration(), session, Instant.EPOCH);
        JobService service = new JobService(
            workers,
            new InMemoryJobRegistry(),
            new ApprovedWorkloadPolicy("GPU_PYTORCH_TEST"),
            mock(WorkerMessageSender.class),
            Clock.systemUTC()
        );
        service.dispatch("worker-001", "GPU_PYTORCH_TEST");

        assertThatThrownBy(() -> service.dispatch("worker-001", "GPU_PYTORCH_TEST"))
            .isInstanceOf(JobConflictException.class);
    }

    @Test
    void activeConnectionLossMarksDispatchedJobResultUnknown() {
        WorkerRegistry workers = new WorkerRegistry();
        WebSocketSession session = mock(WebSocketSession.class);
        when(session.getId()).thenReturn("session-A");
        workers.register(registration(), session, Instant.EPOCH);
        InMemoryJobRegistry jobs = new InMemoryJobRegistry();
        JobService service = new JobService(
            workers, jobs, new ApprovedWorkloadPolicy("GPU_PYTORCH_TEST"),
            mock(WorkerMessageSender.class), Clock.fixed(Instant.EPOCH, ZoneOffset.UTC)
        );
        JobRecord dispatched = service.dispatch("worker-001", "GPU_PYTORCH_TEST");

        assertThat(service.workerDisconnected("worker-001", "session-A")).isTrue();

        assertThat(service.findById(dispatched.jobId()).status())
            .isEqualTo(JobStatus.RESULT_UNKNOWN);
    }

    @Test
    void activeSessionMarksTimedJobRunningWithContainerProof() {
        WorkerRegistry workers = new WorkerRegistry();
        WebSocketSession session = mock(WebSocketSession.class);
        when(session.getId()).thenReturn("session-A");
        workers.register(timedRegistration("worker-001"), session, Instant.EPOCH);
        JobService service = new JobService(
            workers, new InMemoryJobRegistry(),
            new ApprovedWorkloadPolicy("GPU_PYTORCH_TEST_30S"),
            mock(WorkerMessageSender.class), Clock.fixed(Instant.EPOCH, ZoneOffset.UTC)
        );
        JobRecord job = service.dispatch("worker-001", "GPU_PYTORCH_TEST_30S");

        JobProgressResult result = service.started("worker-001", "session-A",
            new ExecuteStartedMessage(
                WorkerMessageType.EXECUTE_STARTED, job.jobId(), "worker-001", "cid",
                "gpu-poc-job-" + job.jobId(), "GPU_PYTORCH_TEST_30S",
                "le3560/image@sha256:" + "a".repeat(64)
            ));

        JobRecord running = service.findById(job.jobId());
        assertThat(result).isEqualTo(JobProgressResult.APPLIED);
        assertThat(running.status()).isEqualTo(JobStatus.RUNNING);
        assertThat(running.containerId()).isEqualTo("cid");
        assertThat(running.containerName()).isEqualTo("gpu-poc-job-" + job.jobId());
    }

    @Test
    void staleSessionCannotMarkReplacementJobRunning() {
        WorkerRegistry workers = new WorkerRegistry();
        WebSocketSession sessionA = mock(WebSocketSession.class);
        WebSocketSession sessionB = mock(WebSocketSession.class);
        when(sessionA.getId()).thenReturn("session-A");
        when(sessionB.getId()).thenReturn("session-B");
        workers.register(timedRegistration("worker-001"), sessionA, Instant.EPOCH);
        workers.register(timedRegistration("worker-001"), sessionB, Instant.EPOCH.plusSeconds(1));
        JobService service = new JobService(
            workers, new InMemoryJobRegistry(),
            new ApprovedWorkloadPolicy("GPU_PYTORCH_TEST_30S"),
            mock(WorkerMessageSender.class), Clock.systemUTC()
        );
        JobRecord job = service.dispatch("worker-001", "GPU_PYTORCH_TEST_30S");
        ExecuteStartedMessage message = new ExecuteStartedMessage(
            WorkerMessageType.EXECUTE_STARTED, job.jobId(), "worker-001", "cid",
            "gpu-poc-job-" + job.jobId(), "GPU_PYTORCH_TEST_30S",
            "le3560/image@sha256:" + "a".repeat(64)
        );

        assertThat(service.started("worker-001", "session-A", message))
            .isEqualTo(JobProgressResult.STALE_SESSION);
        assertThat(service.findById(job.jobId()).status()).isEqualTo(JobStatus.DISPATCHED);
    }

    @Test
    void mismatchedContainerProofCannotCompleteTimedJob() {
        WorkerRegistry workers = new WorkerRegistry();
        WebSocketSession session = mock(WebSocketSession.class);
        when(session.getId()).thenReturn("session-A");
        workers.register(timedRegistration("worker-001"), session, Instant.EPOCH);
        JobService service = new JobService(
            workers, new InMemoryJobRegistry(),
            new ApprovedWorkloadPolicy("GPU_PYTORCH_TEST_30S"),
            mock(WorkerMessageSender.class), Clock.systemUTC()
        );
        JobRecord job = service.dispatch("worker-001", "GPU_PYTORCH_TEST_30S");
        service.started("worker-001", "session-A", new ExecuteStartedMessage(
            WorkerMessageType.EXECUTE_STARTED, job.jobId(), "worker-001", "cid-good",
            "gpu-poc-job-" + job.jobId(), "GPU_PYTORCH_TEST_30S",
            "le3560/image@sha256:" + "a".repeat(64)
        ));
        ExecuteResultMessage mismatch = new ExecuteResultMessage(
            WorkerMessageType.EXECUTE_RESULT, job.jobId(), ExecuteResultStatus.SUCCESS,
            "NVIDIA Test GPU", 30_000L, "b".repeat(64), 0, true, null, null,
            "cid-other", "gpu-poc-job-" + job.jobId(), "GPU_PYTORCH_TEST_30S",
            "le3560/image@sha256:" + "a".repeat(64), 10, 8192, 30
        );

        assertThat(service.complete("worker-001", "session-A", mismatch))
            .isEqualTo(com.gpucompute.poc.backend.registry.JobCompletionResult.STALE_JOB);
        assertThat(service.findById(job.jobId()).status()).isEqualTo(JobStatus.RUNNING);
        assertThat(workers.findById("worker-001").orElseThrow().workerStatus())
            .isEqualTo(WorkerStatus.BUSY);
    }

    private static WorkerRegistration registration() {
        return new WorkerRegistration(
            "worker-001", "NVIDIA Test GPU", 8188, 7800, 0, true,
            Set.of("GPU_PYTORCH_TEST"), 600, WorkerStatus.IDLE
        );
    }

    private static WorkerRegistration timedRegistration(String workerId) {
        return new WorkerRegistration(
            workerId, "NVIDIA Test GPU", 8188, 7800, 0, true,
            Set.of("GPU_PYTORCH_TEST_30S"), 600, WorkerStatus.IDLE
        );
    }
}
