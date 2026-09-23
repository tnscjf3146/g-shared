package com.gshared.back.job.service;

import com.gshared.back.global.blockchain.EscrowChainClient;
import com.gshared.back.global.blockchain.EscrowJobState;
import com.gshared.back.job.domain.Job;
import com.gshared.back.job.dto.response.JobOnchainResponse;
import com.gshared.back.job.exception.JobErrorCode;
import com.gshared.back.job.repository.JobRepository;
import com.gshared.back.global.exception.BusinessException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;
import org.junit.jupiter.params.provider.NullSource;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.junit.jupiter.api.extension.ExtendWith;
import org.springframework.test.util.ReflectionTestUtils;

import java.math.BigInteger;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class JobOnchainVerificationServiceTest {

    private static final long OWNER_ID = 7L;
    private static final long JOB_ID = 13L;
    private static final String CONTRACT = "0x" + "1".repeat(40);
    private static final String CLIENT = "0x" + "2".repeat(40);
    private static final String PROVIDER = "0x" + "3".repeat(40);
    private static final String RESULT_HASH = "0x" + "ab".repeat(32);

    @Mock JobRepository jobs;
    @Mock EscrowChainClient chain;

    private JobOnchainVerificationService service;
    private Job job;

    @BeforeEach
    void setUp() {
        service = new JobOnchainVerificationService(jobs, chain);
        job = Job.create(null, 1L, 1L, 1L, "AUTO", 1, 1, 0.0001, 512,
                1.0, "test", "worker-test", 1L);
        ReflectionTestUtils.setField(job, "id", JOB_ID);
        ReflectionTestUtils.setField(job, "escrowChainId", 31221L);
        ReflectionTestUtils.setField(job, "escrowContractAddress", CONTRACT);
        ReflectionTestUtils.setField(job, "escrowJobId", "11");
        ReflectionTestUtils.setField(job, "fundTxHash", "0xfund");
        ReflectionTestUtils.setField(job, "submitTxHash", "0xsubmit");
        when(jobs.findByIdAndOwnerId(JOB_ID, OWNER_ID)).thenReturn(Optional.of(job));
    }

    @ParameterizedTest
    @EnumSource(value = EscrowJobState.class, names = {"FUNDED", "ASSIGNED", "COMPLETED", "CANCELLED"})
    void returnsActualOnchainJobFields(EscrowJobState state) {
        when(chain.readJob(CONTRACT, "11")).thenReturn(Optional.of(onchain(state)));
        when(chain.latestBlockNumber()).thenReturn(Optional.of(123456L));
        when(chain.readReceipt("0xfund")).thenReturn(found(true, 10L));
        when(chain.readReceipt("0xsubmit")).thenReturn(found(false, 20L));

        JobOnchainResponse response = service.get(OWNER_ID, JOB_ID);

        assertThat(response.available()).isTrue();
        assertThat(response.jobId()).isEqualTo(JOB_ID);
        assertThat(response.escrowJobId()).isEqualTo("11");
        assertThat(response.chain().chainId()).isEqualTo(31221L);
        assertThat(response.chain().contractAddress()).isEqualTo(CONTRACT);
        assertThat(response.onchain().state()).isEqualTo(state);
        assertThat(response.onchain().client()).isEqualTo(CLIENT);
        assertThat(response.onchain().provider()).isEqualTo(PROVIDER);
        assertThat(response.onchain().price()).isEqualTo("100000");
        assertThat(response.onchain().resultHash()).isEqualTo(RESULT_HASH);
        assertThat(response.evidence().latestBlockNumber()).isEqualTo(123456L);
        assertThat(response.evidence().verifiedAt()).isBeforeOrEqualTo(LocalDateTime.now());
        assertThat(response.evidence().source()).isEqualTo("BLOCKCHAIN_RPC");
        assertThat(response.transactions()).extracting(JobOnchainResponse.Transaction::type)
                .containsExactly("FUND", "SUBMIT");
        assertThat(response.transactions()).extracting(JobOnchainResponse.Transaction::status)
                .containsExactly("SUCCESS", "REVERTED");
    }

    @Test
    void returnsUnavailableWhenEscrowJobIdIsMissing() {
        ReflectionTestUtils.setField(job, "escrowJobId", null);

        JobOnchainResponse response = service.get(OWNER_ID, JOB_ID);

        assertThat(response.available()).isFalse();
        assertThat(response.reason()).isEqualTo("ESCROW_JOB_ID_MISSING");
        verifyNoInteractions(chain);
    }

    @Test
    void returnsUnavailableWhenRpcCannotReadTheJob() {
        when(chain.readJob(CONTRACT, "11")).thenReturn(Optional.empty());

        JobOnchainResponse response = service.get(OWNER_ID, JOB_ID);

        assertThat(response.available()).isFalse();
        assertThat(response.reason()).isEqualTo("BLOCKCHAIN_RPC_UNAVAILABLE");
    }

    @Test
    void returnsUnavailableWhenRecordedContractIsMissing() {
        ReflectionTestUtils.setField(job, "escrowContractAddress", null);

        JobOnchainResponse response = service.get(OWNER_ID, JOB_ID);

        assertThat(response.available()).isFalse();
        assertThat(response.reason()).isEqualTo("ESCROW_CONTRACT_MISSING");
        verifyNoInteractions(chain);
    }

    @Test
    void reportsPendingAndUnavailableReceiptsWithoutHidingTheOnchainJob() {
        ReflectionTestUtils.setField(job, "acceptTxHash", "0xaccept");
        when(chain.readJob(CONTRACT, "11")).thenReturn(Optional.of(onchain(EscrowJobState.ASSIGNED)));
        when(chain.latestBlockNumber()).thenReturn(Optional.of(1L));
        when(chain.readReceipt("0xfund")).thenReturn(new EscrowChainClient.ReceiptLookup(
                EscrowChainClient.ReceiptLookup.Status.NOT_FOUND, null));
        when(chain.readReceipt("0xaccept")).thenReturn(new EscrowChainClient.ReceiptLookup(
                EscrowChainClient.ReceiptLookup.Status.UNREACHABLE, null));
        when(chain.readReceipt("0xsubmit")).thenReturn(found(true, 2L));

        JobOnchainResponse response = service.get(OWNER_ID, JOB_ID);

        assertThat(response.available()).isTrue();
        assertThat(response.transactions()).extracting(JobOnchainResponse.Transaction::status)
                .containsExactly("NOT_FOUND", "RPC_UNAVAILABLE", "SUCCESS");
        verify(chain, never()).findFundedJob(anyString());
    }

    @Test
    void refusesAnotherUsersJob() {
        when(jobs.findByIdAndOwnerId(JOB_ID, OWNER_ID)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.get(OWNER_ID, JOB_ID))
                .isInstanceOf(BusinessException.class)
                .extracting("errorCode").isEqualTo(JobErrorCode.JOB_NOT_FOUND);
        verifyNoInteractions(chain);
    }

    @ParameterizedTest
    @NullSource
    void omitsMissingTransactionHashes(String ignored) {
        ReflectionTestUtils.setField(job, "fundTxHash", null);
        ReflectionTestUtils.setField(job, "submitTxHash", null);
        when(chain.readJob(CONTRACT, "11")).thenReturn(Optional.of(onchain(EscrowJobState.COMPLETED)));
        when(chain.latestBlockNumber()).thenReturn(Optional.of(1L));

        JobOnchainResponse response = service.get(OWNER_ID, JOB_ID);

        assertThat(response.transactions()).isEmpty();
        verify(chain, never()).readReceipt(anyString());
    }

    private static EscrowChainClient.OnChainJob onchain(EscrowJobState state) {
        return new EscrowChainClient.OnChainJob(
                CLIENT, state, 2_000_000_000L, PROVIDER,
                "0x" + "4".repeat(40), new BigInteger("100000"), RESULT_HASH);
    }

    private static EscrowChainClient.ReceiptLookup found(boolean success, long blockNumber) {
        return new EscrowChainClient.ReceiptLookup(
                EscrowChainClient.ReceiptLookup.Status.FOUND,
                new EscrowChainClient.Receipt(success, CONTRACT, blockNumber, List.of()));
    }
}
