package com.gshared.back.job.service;

import com.gshared.back.global.blockchain.EscrowChainClient;
import com.gshared.back.global.exception.BusinessException;
import com.gshared.back.job.domain.Job;
import com.gshared.back.job.dto.response.JobOnchainResponse;
import com.gshared.back.job.exception.JobErrorCode;
import com.gshared.back.job.repository.JobRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;

@Service
@RequiredArgsConstructor
@Transactional(readOnly = true)
public class JobOnchainVerificationService {
    private final JobRepository jobRepository;
    private final EscrowChainClient chainClient;

    public JobOnchainResponse get(Long ownerId, Long jobId) {
        Job job = jobRepository.findByIdAndOwnerId(jobId, ownerId)
                .orElseThrow(() -> new BusinessException(JobErrorCode.JOB_NOT_FOUND));
        if (job.getEscrowJobId() == null || job.getEscrowJobId().isBlank()) {
            return JobOnchainResponse.unavailable(jobId, null, "ESCROW_JOB_ID_MISSING");
        }
        if (job.getEscrowContractAddress() == null || job.getEscrowContractAddress().isBlank()) {
            return JobOnchainResponse.unavailable(jobId, job.getEscrowJobId(), "ESCROW_CONTRACT_MISSING");
        }
        var found = chainClient.readJob(job.getEscrowContractAddress(), job.getEscrowJobId());
        if (found.isEmpty()) {
            return JobOnchainResponse.unavailable(jobId, job.getEscrowJobId(), "BLOCKCHAIN_RPC_UNAVAILABLE");
        }

        var onchain = found.get();
        List<JobOnchainResponse.Transaction> transactions = new ArrayList<>();
        addReceipt(transactions, "FUND", job.getFundTxHash());
        addReceipt(transactions, "ACCEPT", job.getAcceptTxHash());
        addReceipt(transactions, "SUBMIT", job.getSubmitTxHash());
        addReceipt(transactions, "ABORT", job.getAbortTxHash());
        return new JobOnchainResponse(true, null, jobId, job.getEscrowJobId(),
                new JobOnchainResponse.Chain(job.getEscrowChainId(), job.getEscrowContractAddress()),
                new JobOnchainResponse.Onchain(onchain.state(), onchain.client(), onchain.provider(),
                        onchain.operator(), onchain.priceWei().toString(), onchain.resultHash(), onchain.deadline()),
                new JobOnchainResponse.Evidence(chainClient.latestBlockNumber().orElse(null),
                        LocalDateTime.now(), "BLOCKCHAIN_RPC"),
                List.copyOf(transactions));
    }

    private void addReceipt(List<JobOnchainResponse.Transaction> target, String type, String txHash) {
        if (txHash == null || txHash.isBlank()) return;
        EscrowChainClient.ReceiptLookup lookup = chainClient.readReceipt(txHash);
        String status;
        Long blockNumber = null;
        if (lookup.status() == EscrowChainClient.ReceiptLookup.Status.FOUND) {
            status = lookup.receipt().success() ? "SUCCESS" : "REVERTED";
            blockNumber = lookup.receipt().blockNumber();
        } else if (lookup.status() == EscrowChainClient.ReceiptLookup.Status.NOT_FOUND) {
            status = "NOT_FOUND";
        } else {
            status = "RPC_UNAVAILABLE";
        }
        target.add(new JobOnchainResponse.Transaction(type, txHash, blockNumber, status));
    }
}
