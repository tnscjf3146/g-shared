package com.gshared.back.job.repository;

import com.gshared.back.job.domain.Job;
import com.gshared.back.global.config.JpaAuditingConfig;
import org.springframework.context.annotation.Import;
import com.gshared.back.job.domain.JobStatus;
import com.gshared.back.user.domain.User;
import jakarta.persistence.EntityManager;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.data.jpa.test.autoconfigure.DataJpaTest;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.util.ReflectionTestUtils;

import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;
import com.gshared.back.global.blockchain.*;
import com.gshared.back.job.service.*;
import com.gshared.back.worker.repository.WorkerRepository;
import java.util.Optional;
import java.util.List;

@DataJpaTest(properties = {"spring.flyway.enabled=false", "spring.jpa.hibernate.ddl-auto=create-drop"})
@ActiveProfiles("refund-test")
@Import(JpaAuditingConfig.class)
class JobRefundRecoveryRepositoryTest {
    @Autowired JobRepository jobs;
    @Autowired EntityManager em;
    private Job job;
    private Long id;

    @BeforeEach
    void setUp() {
        User owner = User.createUser("refund@example.com", "unused", "client");
        owner.linkWallet("0x" + "a".repeat(40));
        em.persist(owner);
        job = Job.create(owner, 1L, 1L, 1L, "AUTO", 1, 16, 0.0002, 512,
                100.0, "test", "worker", "0x" + "b".repeat(40), 9L);
        ReflectionTestUtils.setField(job, "escrowJobId", "17");
        ReflectionTestUtils.setField(job, "assignedWorkerId", "worker");
        ReflectionTestUtils.setField(job, "status", JobStatus.FAILED);
        ReflectionTestUtils.setField(job, "failReason", "RUNTIME_ERROR: training failed");
        ReflectionTestUtils.setField(job, "finishedAt", LocalDateTime.now().minusDays(1));
        jobs.saveAndFlush(job);
        id = job.getId();
        em.clear();
    }

    @Test
    void historicalFailureIsSelectedEvenWithoutReconciliationFlagOrAbortHash() {
        assertThat(jobs.findRefundRecoveryCandidates()).extracting(Job::getId).containsExactly(id);
        assertThat(jobs.reopenFailedForRefund(id, "refund pending")).isOne();
        Job held = jobs.findById(id).orElseThrow();
        assertThat(held.getStatus()).isEqualTo(JobStatus.CANCELLING);
        assertThat(held.getFinishedAt()).isNull();
        assertThat(held.getFailReason()).contains("RUNTIME_ERROR");
        assertThat(jobs.findOpenOnChain()).extracting(Job::getId).contains(id);
        assertThat(jobs.findFundedWithOwner()).isEmpty();
        assertThat(jobs.reopenFailedForRefund(id, "again")).isZero();
    }

    @Test
    void confirmedTerminalRefundIsNotScannedAgain() {
        assertThat(jobs.confirmRefundIfTerminal(id, LocalDateTime.now())).isOne();
        assertThat(jobs.findRefundRecoveryCandidates()).isEmpty();
        assertThat(jobs.findById(id).orElseThrow().getStatus()).isEqualTo(JobStatus.FAILED);
        assertThat(jobs.reopenFailedForRefund(id, "again")).isZero();
    }

    @Test
    void chainSettlementPreservesFailureReasonAndExistingTransaction() {
        Job existing = jobs.findById(id).orElseThrow();
        ReflectionTestUtils.setField(existing, "abortTxHash", "0xoriginal");
        jobs.flush();
        jobs.reopenFailedForRefund(id, "pending");
        assertThat(jobs.cancelByChainIfOpen(id, null, "PROVIDER_TIMEOUT", LocalDateTime.now())).isOne();
        Job closed = jobs.findById(id).orElseThrow();
        assertThat(closed.getStatus()).isEqualTo(JobStatus.CANCELLED);
        assertThat(closed.getFailReason()).isEqualTo("RUNTIME_ERROR: training failed");
        assertThat(closed.getAbortTxHash()).isEqualTo("0xoriginal");
        assertThat(closed.getRefundConfirmedAt()).isNotNull();
    }

    @Test
    void ordinaryCancellationDoesNotBecomeTrainingFailure() {
        Job existing = jobs.findById(id).orElseThrow();
        ReflectionTestUtils.setField(existing, "status", JobStatus.CANCELLING);
        ReflectionTestUtils.setField(existing, "failReason", null);
        jobs.flush();
        jobs.cancelByChainIfOpen(id, "0xrefund", "PROVIDER_TIMEOUT", LocalDateTime.now());
        assertThat(jobs.findById(id).orElseThrow().getFailReason()).isNull();
    }

    @Test
    void failureWhileCancellingIsRecordedAndKeptInKeeperQueue() {
        Job existing = jobs.findById(id).orElseThrow();
        ReflectionTestUtils.setField(existing, "status", JobStatus.CANCELLING);
        ReflectionTestUtils.setField(existing, "failReason", null);
        jobs.flush();
        assertThat(jobs.holdFailureForRefund(id, "worker", "OOM", 12.0, "pending")).isOne();
        Job held = jobs.findById(id).orElseThrow();
        assertThat(held.getFailReason()).isEqualTo("OOM");
        assertThat(held.getActualRuntimeSec()).isEqualTo(12.0);
        assertThat(jobs.holdFailureForRefund(id, "other-worker", "wrong", 20.0, "pending")).isZero();
        assertThat(jobs.holdFailureForRefund(id, "worker", "later report", 20.0, "pending")).isOne();
        assertThat(jobs.findById(id).orElseThrow().getFailReason()).isEqualTo("OOM");
    }

    @Test
    void historicalFailureRecoversThroughKeeperOnlyAfterDeadline() {
        Job existing = jobs.findById(id).orElseThrow();
        String contract = "0x" + "c".repeat(40);
        ReflectionTestUtils.setField(existing, "escrowChainId", 31221L);
        ReflectionTestUtils.setField(existing, "escrowContractAddress", contract);
        jobs.flush();
        String ref = existing.getExternalRef();
        EscrowChainClient chain = mock(EscrowChainClient.class);
        EscrowVerification escrow = mock(EscrowVerification.class);
        EscrowKeeperSigner signer = mock(EscrowKeeperSigner.class);
        EscrowSettlementService settlement = new EscrowSettlementService(
                jobs, mock(WorkerRepository.class), mock(JobService.class));
        EscrowRefundRecovery recovery = new EscrowRefundRecovery(jobs, chain, settlement);
        EscrowTimeoutKeeper keeper = new EscrowTimeoutKeeper(jobs, escrow, chain, signer, settlement);
        var locked = new EscrowChainClient.OnChainJob("0x" + "a".repeat(40), EscrowJobState.ASSIGNED,
                100, "0x" + "b".repeat(40), "0x" + "d".repeat(40), TokenUnits.toWei(9), "0x00");
        when(chain.isConfigured()).thenReturn(true);
        when(chain.chainId()).thenReturn(Optional.of(31221L));
        when(chain.jobIdByClientRef(contract, "0x" + "a".repeat(40), ref)).thenReturn(Optional.of("17"));
        when(chain.readJob(contract, "17")).thenReturn(Optional.of(locked));
        recovery.recover();
        assertThat(jobs.findById(id).orElseThrow().getStatus()).isEqualTo(JobStatus.CANCELLING);

        when(signer.isEnabled()).thenReturn(true);
        when(chain.latestBlockTimestamp()).thenReturn(Optional.of(100L));
        when(escrow.stateOnChain(contract, "17")).thenReturn(Optional.of(locked));
        keeper.sweep();
        verify(signer, never()).settleTimeout(anyLong(), anyString(), anyString());

        when(chain.latestBlockTimestamp()).thenReturn(Optional.of(101L));
        when(signer.settleTimeout(31221L, contract, "17")).thenReturn(Optional.of("0xrefund"));
        when(chain.readReceipt("0xrefund")).thenReturn(new EscrowChainClient.ReceiptLookup(
                EscrowChainClient.ReceiptLookup.Status.FOUND,
                new EscrowChainClient.Receipt(true, contract, List.of())));
        keeper.sweep();
        verify(signer).settleTimeout(31221L, contract, "17");
        Job closed = jobs.findById(id).orElseThrow();
        assertThat(closed.getStatus()).isEqualTo(JobStatus.CANCELLED);
        assertThat(closed.getRefundConfirmedAt()).isNotNull();
        assertThat(closed.getFailReason()).contains("RUNTIME_ERROR");
        assertThat(jobs.findRefundRecoveryCandidates()).isEmpty();
    }

    @Test
    void paidJobCannotBeReopenedOrMarkedRefunded() {
        Job existing = jobs.findById(id).orElseThrow();
        ReflectionTestUtils.setField(existing, "status", JobStatus.COMPLETED);
        jobs.flush();
        assertThat(jobs.findRefundRecoveryCandidates()).isEmpty();
        assertThat(jobs.reopenFailedForRefund(id, "pending")).isZero();
        assertThat(jobs.confirmRefundIfTerminal(id, LocalDateTime.now())).isZero();
    }
}
