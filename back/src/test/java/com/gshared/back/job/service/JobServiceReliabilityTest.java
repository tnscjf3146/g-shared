package com.gshared.back.job.service;

import com.gshared.back.global.blockchain.EscrowVerification;
import com.gshared.back.job.domain.Job;
import com.gshared.back.job.domain.JobStatus;
import com.gshared.back.job.repository.JobRepository;
import com.gshared.back.worker.domain.Worker;
import com.gshared.back.worker.repository.WorkerRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.context.ApplicationEventPublisher;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class JobServiceReliabilityTest {
    @Mock JobRepository jobs;
    @Mock WorkerRepository workers;
    @Mock EscrowVerification escrow;
    @Mock ApplicationEventPublisher eventPublisher;
    @Mock Job job;
    @Mock Worker worker;
    @InjectMocks JobService service;

    @BeforeEach
    void workerLookup() {
        lenient().when(workers.findById("worker")).thenReturn(Optional.of(worker));
        lenient().when(worker.getCurrentJobId()).thenReturn(7L);
    }

    @Test
    void completedTransitionIncrementsExactlyOnce() {
        completedJobSetup();
        when(jobs.completeIfRunning(eq(7L), eq("worker"), anyString(), anyString(), any(), any(), any()))
                .thenReturn(1, 0);

        assertThat(service.completeJob("worker", 7L, "results/7.tar", "0xabc", "0xtx", 10.0)).isTrue();
        assertThat(service.completeJob("worker", 7L, "results/7.tar", "0xabc", "0xtx", 10.0)).isFalse();

        verify(workers, times(1)).incrementCompletedCount("worker");
    }

    @Test
    void workerRuntimeFailureIncrementsOnlyAfterTerminalTransition() {
        failureSetup();
        when(jobs.failIfRunning(eq(7L), eq("worker"), anyString(), eq("0xabort"), eq(10.0), any()))
                .thenReturn(1, 0);

        assertThat(service.failJob("worker", 7L, "CUDA_OOM", "out of memory", "0xabort", 10.0)).isTrue();
        assertThat(service.failJob("worker", 7L, "CUDA_OOM", "out of memory", "0xabort", 10.0)).isFalse();

        verify(workers, times(1)).incrementFailedCount("worker");
    }

    @Test
    void nonWorkerFailureDoesNotIncrementFailureCount() {
        failureSetup();
        when(jobs.failIfRunning(eq(7L), eq("worker"), anyString(), eq("0xabort"), eq(10.0), any()))
                .thenReturn(1);

        assertThat(service.failJob("worker", 7L, "ESCROW_CANCELLED", "cancelled", "0xabort", 10.0)).isTrue();

        verify(workers, never()).incrementFailedCount(anyString());
    }

    private void completedJobSetup() {
        when(jobs.findByIdAndAssignedWorkerId(7L, "worker")).thenReturn(Optional.of(job));
        when(job.getStatus()).thenReturn(JobStatus.RUNNING);
        when(job.getResultSha256()).thenReturn("abc");
        when(job.getResultObjectKey()).thenReturn("results/7.tar");
        when(escrow.resultOnChain(any(), any(), any(), eq("0xabc")))
                .thenReturn(EscrowVerification.Verdict.CONFIRMED);
    }

    private void failureSetup() {
        when(jobs.findById(7L)).thenReturn(Optional.of(job));
        when(job.getEscrowJobId()).thenReturn("17");
        when(escrow.cancelledOnChain(any(), eq("17"))).thenReturn(EscrowVerification.Verdict.CONFIRMED);
    }
}
