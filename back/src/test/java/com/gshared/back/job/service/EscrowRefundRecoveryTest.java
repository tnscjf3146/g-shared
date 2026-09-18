package com.gshared.back.job.service;

import com.gshared.back.global.blockchain.EscrowChainClient;
import com.gshared.back.global.blockchain.EscrowJobState;
import com.gshared.back.global.blockchain.TokenUnits;
import com.gshared.back.job.domain.Job;
import com.gshared.back.job.repository.JobRepository;
import com.gshared.back.user.domain.User;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.List;
import java.util.Optional;

import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class EscrowRefundRecoveryTest {
    private static final String CLIENT = "0x" + "a".repeat(40);
    private static final String PROVIDER = "0x" + "b".repeat(40);
    private static final String CONTRACT = "0x" + "c".repeat(40);
    @Mock JobRepository jobs;
    @Mock EscrowChainClient chain;
    @Mock EscrowSettlementService settlement;
    private EscrowRefundRecovery recovery;
    private Job job;

    @BeforeEach
    void setUp() {
        User owner = User.createUser("client@example.com", "unused", "client");
        owner.linkWallet(CLIENT);
        job = Job.create(owner, 1L, 1L, 1L, "AUTO", 1, 16, 0.0002, 512,
                100.0, "test", "worker", PROVIDER, 9L);
        ReflectionTestUtils.setField(job, "id", 7L);
        ReflectionTestUtils.setField(job, "escrowChainId", 31221L);
        ReflectionTestUtils.setField(job, "escrowContractAddress", CONTRACT);
        ReflectionTestUtils.setField(job, "escrowJobId", "17");
        recovery = new EscrowRefundRecovery(jobs, chain, settlement);
        when(chain.isConfigured()).thenReturn(true);
        when(chain.chainId()).thenReturn(Optional.of(31221L));
        when(jobs.findRefundRecoveryCandidates()).thenReturn(List.of(job));
    }

    private void bind(EscrowJobState state) {
        when(chain.jobIdByClientRef(CONTRACT, CLIENT, job.getExternalRef())).thenReturn(Optional.of("17"));
        when(chain.readJob(CONTRACT, "17")).thenReturn(Optional.of(new EscrowChainClient.OnChainJob(
                CLIENT, state, 100, PROVIDER, PROVIDER, TokenUnits.toWei(9), "0x" + "0".repeat(64))));
    }

    @ParameterizedTest
    @EnumSource(value = EscrowJobState.class, names = {"FUNDED", "ASSIGNED"})
    void lockedDepositReturnsToKeeperWithoutSendingTransaction(EscrowJobState state) {
        bind(state);
        recovery.recover();
        verify(settlement).recoverRefund(7L);
        verify(settlement, never()).confirmTerminalRefund(anyLong());
        verify(chain, never()).sendRawTransaction(anyString());
    }

    @Test
    void refundedDepositIsRecordedWithoutReopening() {
        bind(EscrowJobState.CANCELLED);
        recovery.recover();
        verify(settlement).confirmTerminalRefund(7L);
        verify(settlement, never()).recoverRefund(anyLong());
    }

    @Test
    void paidDepositIsFlaggedAndNeverRefunded() {
        bind(EscrowJobState.COMPLETED);
        recovery.recover();
        verify(settlement).flag(eq(7L), contains("already paid"));
        verify(settlement, never()).recoverRefund(anyLong());
    }

    @Test
    void differentChainDoesNotEvenReadEscrow() {
        ReflectionTestUtils.setField(job, "escrowChainId", 1L);
        recovery.recover();
        verify(settlement).flag(eq(7L), contains("binding"));
        verify(chain, never()).readJob(anyString(), anyString());
    }

    @Test
    void missingHistoricalContractDoesNotUseCurrentContract() {
        ReflectionTestUtils.setField(job, "escrowContractAddress", null);
        recovery.recover();
        verify(settlement).flag(eq(7L), contains("binding"));
        verify(chain, never()).contractAddress();
        verify(chain, never()).readJob(anyString(), anyString());
    }

    @Test
    void wrongExternalRefCannotReopenAnotherDeposit() {
        when(chain.jobIdByClientRef(CONTRACT, CLIENT, job.getExternalRef())).thenReturn(Optional.of("99"));
        recovery.recover();
        verify(settlement).flag(eq(7L), contains("externalRef"));
        verify(chain, never()).readJob(anyString(), anyString());
    }

    @Test
    void rpcFailureLeavesCandidateForNextSweep() {
        when(chain.jobIdByClientRef(CONTRACT, CLIENT, job.getExternalRef())).thenReturn(Optional.empty());
        recovery.recover();
        verifyNoInteractions(settlement);
    }

    @Test
    void failedStateReadDoesNotConfirmRefund() {
        when(chain.jobIdByClientRef(CONTRACT, CLIENT, job.getExternalRef())).thenReturn(Optional.of("17"));
        when(chain.readJob(CONTRACT, "17")).thenReturn(Optional.empty());
        recovery.recover();
        verifyNoInteractions(settlement);
    }

    @Test
    void mismatchedAmountRequiresManualReview() {
        when(chain.jobIdByClientRef(CONTRACT, CLIENT, job.getExternalRef())).thenReturn(Optional.of("17"));
        when(chain.readJob(CONTRACT, "17")).thenReturn(Optional.of(new EscrowChainClient.OnChainJob(
                CLIENT, EscrowJobState.FUNDED, 100, PROVIDER, PROVIDER, TokenUnits.toWei(10), "0x00")));
        recovery.recover();
        verify(settlement).flag(eq(7L), contains("price mismatch"));
        verify(settlement, never()).recoverRefund(anyLong());
    }
}
