package com.gshared.back.job.realtime;

import com.gshared.back.global.blockchain.EscrowVerification;
import com.gshared.back.job.domain.Job;
import com.gshared.back.job.domain.JobStatus;
import com.gshared.back.job.repository.JobRepository;
import com.gshared.back.job.service.JobService;
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
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class JobRealtimeTerminalTest {
    @Mock JobRepository jobs;
    @Mock WorkerRepository workers;
    @Mock EscrowVerification escrow;
    @Mock ApplicationEventPublisher events;
    @Mock Job job;
    @InjectMocks JobService service;

    @BeforeEach
    void jobLookup() {
        org.mockito.Mockito.lenient().when(jobs.findById(13L)).thenReturn(Optional.of(job));
    }

    @Test
    void completedTransitionPublishesExactlyOnce() {
        when(jobs.findByIdAndAssignedWorkerId(13L, "worker")).thenReturn(Optional.of(job));
        when(job.getStatus()).thenReturn(JobStatus.RUNNING);
        when(job.getResultSha256()).thenReturn("abc");
        when(job.getResultObjectKey()).thenReturn("results/13.tar");
        when(escrow.resultOnChain(any(), any(), any(), eq("0xabc")))
                .thenReturn(EscrowVerification.Verdict.CONFIRMED);
        when(jobs.completeIfRunning(eq(13L), eq("worker"), anyString(), anyString(), any(), any(), any()))
                .thenReturn(1, 0);

        assertThat(service.completeJob("worker", 13L, "results/13.tar", "0xabc", "0xtx", 10.0)).isTrue();
        assertThat(service.completeJob("worker", 13L, "results/13.tar", "0xabc", "0xtx", 10.0)).isFalse();

        verify(events, times(1)).publishEvent((Object) org.mockito.ArgumentMatchers.argThat(value ->
                value instanceof JobRealtimeEvent event && event.type().equals("JOB_COMPLETED")));
    }

    @Test
    void failedTransitionPublishesSanitizedCode() {
        when(jobs.failIfRunning(eq(13L), eq("worker"), anyString(), eq("0xabort"), eq(10.0), any()))
                .thenReturn(1);

        assertThat(service.failJob("worker", 13L, "CUDA_OOM", "secret detail", "0xabort", 10.0)).isTrue();

        verify(events).publishEvent((Object) org.mockito.ArgumentMatchers.argThat(value ->
                value instanceof JobRealtimeEvent event
                        && event.type().equals("JOB_FAILED")
                        && event.reason().equals("CUDA_OOM")));
    }

    @Test
    void cancelledTransitionPublishesEvent() {
        when(jobs.confirmCancelledIfCancelling(eq(13L), eq("worker"), eq("0xabort"), eq(10.0), any()))
                .thenReturn(1);

        assertThat(service.confirmCancelled("worker", 13L, "0xabort", 10.0)).isTrue();

        verify(events).publishEvent((Object) org.mockito.ArgumentMatchers.argThat(value ->
                value instanceof JobRealtimeEvent event && event.type().equals("JOB_CANCELLED")));
    }
}
