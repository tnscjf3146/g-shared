package com.gshared.back.job.dto.response;

import com.gshared.back.job.domain.Job;

/**
 * 예치 확인 응답.
 *
 * <p>{@code status} 는 FUNDED(체인 대조 끝, 매칭 대기) 또는 PENDING(영수증이 아직 없거나 노드가 답하지 않아
 * 보류 — reconciliation 이 다시 확인하고, 프런트도 같은 txHash 로 다시 물을 수 있다). 같은 txHash 재전송은
 * 같은 답을 돌려준다(멱등).
 */
public record FundJobResponse(
        Long jobId,
        String status,
        String txHash,
        String escrowJobId
) {
    public static final String FUNDED = "FUNDED";
    public static final String PENDING = "PENDING";

    public static FundJobResponse funded(Job job) {
        return new FundJobResponse(job.getId(), FUNDED, job.getFundTxHash(), job.getEscrowJobId());
    }

    public static FundJobResponse pending(Long jobId, String txHash) {
        return new FundJobResponse(jobId, PENDING, txHash, null);
    }
}
