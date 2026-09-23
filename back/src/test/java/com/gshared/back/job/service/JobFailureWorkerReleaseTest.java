package com.gshared.back.job.service;

import com.gshared.back.global.blockchain.EscrowVerification;
import com.gshared.back.global.blockchain.EscrowVerification.Verdict;
import com.gshared.back.job.domain.Job;
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
class JobFailureWorkerReleaseTest {
    @Mock JobRepository jobRepository;
    @Mock WorkerRepository workerRepository;
    @Mock EscrowVerification escrow;
    @Mock Job job;
    @Mock Worker worker;
    @Mock ApplicationEventPublisher eventPublisher;
    @InjectMocks JobService service;

    @BeforeEach
    void setUp() {
        when(jobRepository.findById(7L)).thenReturn(Optional.of(job));
        when(job.getEscrowJobId()).thenReturn("17");
        when(workerRepository.findById("worker")).thenReturn(Optional.of(worker));
    }

    @Test
    void unrefundedFailureFreesOnlyItsOwnWorker() {
        when(escrow.cancelledOnChain(null, "17")).thenReturn(Verdict.CONTRADICTED);
        when(jobRepository.holdFailureForRefund(eq(7L), eq("worker"), anyString(), eq(12.0), anyString())).thenReturn(1);
        when(worker.getCurrentJobId()).thenReturn(7L);
        assertThat(service.failJob("worker", 7L, "OOM", "failed", null, 12.0)).isTrue();
        verify(worker).finishJob();
    }

    @Test
    void repeatedFailureCannotFreeWorkerAssignedToAnotherJob() {
        when(escrow.cancelledOnChain(null, "17")).thenReturn(Verdict.CONTRADICTED);
        when(jobRepository.holdFailureForRefund(eq(7L), eq("worker"), anyString(), eq(12.0), anyString())).thenReturn(1);
        when(worker.getCurrentJobId()).thenReturn(8L);
        assertThat(service.failJob("worker", 7L, "OOM", "failed", null, 12.0)).isTrue();
        verify(worker, never()).finishJob();
    }

    @Test
    void delayedRefundConfirmationCannotFreeNewJobEither() {
        when(escrow.cancelledOnChain(null, "17")).thenReturn(Verdict.CONFIRMED);
        when(jobRepository.cancelIfFailedWhileCancelling(eq(7L), eq("worker"), anyString(), isNull(), eq(12.0), anyString(), any())).thenReturn(1);
        when(worker.getCurrentJobId()).thenReturn(8L);
        assertThat(service.failJob("worker", 7L, "OOM", "failed", null, 12.0)).isTrue();
        verify(worker, never()).finishJob();
    }
}
