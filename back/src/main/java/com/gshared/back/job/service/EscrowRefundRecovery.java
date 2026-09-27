package com.gshared.back.job.service;

import com.gshared.back.global.blockchain.EscrowChainClient;
import com.gshared.back.global.blockchain.TokenUnits;
import com.gshared.back.job.domain.Job;
import com.gshared.back.job.repository.JobRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * FAILED/CANCELLED로 먼저 닫혀 환불 순찰에서 빠진 예치를 복구한다.
 * 새 JOB_REJECTED와 배포 전 누락 건에 같은 검증을 적용한다. 체인 쓰기는 기존 keeper만 담당한다.
 * RPC/메타데이터가 불확실하면 재실행하거나 환불 완료로 추정하지 않는다.
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class EscrowRefundRecovery {
    private final JobRepository jobs;
    private final EscrowChainClient chain;
    private final EscrowSettlementService settlement;

    @Scheduled(fixedDelay = 120_000, initialDelay = 20_000)
    public void recover() {
        if (!chain.isConfigured()) {
            return;
        }
        var chainId = chain.chainId();
        if (chainId.isEmpty()) {
            return;
        }
        for (Job job : jobs.findRefundRecoveryCandidates()) {
            try {
                recoverOne(job, chainId.get());
            } catch (Exception e) {
                log.error("[REFUND] 종료 작업 대조 실패 jobId={} (다음 작업 계속)", job.getId(), e);
            }
        }
    }

    private void recoverOne(Job job, long chainId) {
        // 과거 계약 주소가 없는 행을 현재 계약 주소로 추측하지 않는다.
        if (job.getEscrowChainId() == null || job.getEscrowChainId() != chainId
                || job.getEscrowContractAddress() == null || job.getEscrowContractAddress().isBlank()
                || job.getOwner().getWalletAddress() == null || job.getProviderWallet() == null
                || job.getExternalRef() == null || job.getEstimatedCost() == null) {
            settlement.flag(job.getId(), "REFUND_RECOVERY: missing or mismatched escrow binding; manual check required");
            return;
        }
        String contract = job.getEscrowContractAddress();
        String client = job.getOwner().getWalletAddress();
        var mappedId = chain.jobIdByClientRef(contract, client, job.getExternalRef());
        if (mappedId.isEmpty()) {
            return; // RPC 장애: 다음 순찰에서 다시 확인한다.
        }
        if (!mappedId.get().equals(job.getEscrowJobId())) {
            settlement.flag(job.getId(), "REFUND_RECOVERY: externalRef does not identify the recorded escrow job");
            return;
        }
        var read = chain.readJob(contract, job.getEscrowJobId());
        if (read.isEmpty()) {
            return;
        }
        var onChain = read.get();
        if (!onChain.clientIs(client) || !onChain.providerIs(job.getProviderWallet())
                || !TokenUnits.toWei(job.getEstimatedCost()).equals(onChain.priceWei())) {
            settlement.flag(job.getId(), "REFUND_RECOVERY: client/provider/price mismatch; manual check required");
            return;
        }
        switch (onChain.state()) {
            case FUNDED, ASSIGNED -> settlement.recoverRefund(job.getId());
            case CANCELLED -> settlement.confirmTerminalRefund(job.getId());
            case COMPLETED -> settlement.flag(job.getId(),
                    "REFUND_RECOVERY: already paid on-chain; automatic refund is unavailable");
            case NONE -> settlement.flag(job.getId(), "REFUND_RECOVERY: escrow job does not exist");
        }
    }
}
