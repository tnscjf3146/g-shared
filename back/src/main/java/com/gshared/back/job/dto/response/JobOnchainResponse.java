package com.gshared.back.job.dto.response;

import com.gshared.back.global.blockchain.EscrowJobState;

import java.time.LocalDateTime;
import java.util.List;

/** 실제 에스크로 RPC에서 읽은 Job 상태와 저장된 트랜잭션의 영수증 증거. */
public record JobOnchainResponse(
        boolean available,
        String reason,
        Long jobId,
        String escrowJobId,
        Chain chain,
        Onchain onchain,
        Evidence evidence,
        List<Transaction> transactions
) {
    public static JobOnchainResponse unavailable(Long jobId, String escrowJobId, String reason) {
        return new JobOnchainResponse(false, reason, jobId, escrowJobId,
                null, null, null, List.of());
    }

    public record Chain(Long chainId, String contractAddress) {}

    public record Onchain(EscrowJobState state, String client, String provider, String operator,
                          String price, String resultHash, long deadline) {}

    public record Evidence(Long latestBlockNumber, LocalDateTime verifiedAt, String source) {}

    public record Transaction(String type, String txHash, Long blockNumber, String status) {}
}
