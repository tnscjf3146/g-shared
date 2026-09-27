package com.gshared.back.job.dto.response;

import com.gshared.back.global.blockchain.TokenUnits;
import com.gshared.back.job.domain.Job;

/**
 * Job 생성 응답 = 견적서 + 예치 트랜잭션 입력값.
 * CREATED 상태의 Job 행이 곧 견적 — 사용자는 이 응답을 보고 예치(승인) 여부를 결정한다.
 *
 * <p>프런트는 여기 값으로 MetaMask 트랜잭션 두 개를 만든다:
 * {@code GSC.approve(escrowAddress, priceWei)} 와
 * {@code escrow.createAndFundJob(externalRef, priceWei, providerWallet)}.
 * 금액(priceWei)은 JS 정수 범위를 넘으므로 10진 문자열이다. providerWallet 은 예치 뒤 체인이 잠그는 값이라
 * 견적 시점에 확정된 지갑을 그대로 실어야 한다 — 다른 지갑으로 예치하면 예치 확인이 거부한다.
 */
public record CreateJobResponse(
        Long jobId,
        String recommendedWorkerId,
        Double predictedRuntimeSec,
        Long estimatedCost,
        String schedulerModelVersion,
        String adapterName,            // 서버가 확정한 결과 어댑터 이름 (요청을 비워 보냈으면 기본값)
        Integer adapterVersion,        // 서버가 정한 버전 — 이어서 학습이면 부모 + 1
        String externalRef,
        String priceWei,
        String providerWallet,
        String clientWallet,
        Long chainId,
        String tokenAddress,
        String escrowAddress
) {
    public static CreateJobResponse of(Job job, Long chainId, String tokenAddress, String escrowAddress) {
        return new CreateJobResponse(
                job.getId(),
                job.getRecommendedWorkerId(),
                job.getPredictedRuntimeSec(),
                job.getEstimatedCost(),
                job.getSchedulerModelVersion(),
                job.getAdapterName(),
                job.getAdapterVersion(),
                job.getExternalRef(),
                TokenUnits.toWei(job.getEstimatedCost()).toString(),
                job.getProviderWallet(),
                job.getOwner().getWalletAddress(),
                chainId,
                tokenAddress,
                escrowAddress
        );
    }
}
