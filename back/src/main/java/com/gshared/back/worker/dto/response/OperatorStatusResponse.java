package com.gshared.back.worker.dto.response;

import com.gshared.back.worker.domain.OperatorApproval;
import com.gshared.back.worker.domain.Worker;

/**
 * 공급자가 이 워커의 operator 키를 온체인에서 승인했는가 — 지금 체인에 물어본 그대로.
 *
 * <p>{@code approved} 는 {@code status == APPROVED} 의 줄임이다. 화면은 대개 이 한 값만 보면 되고,
 * false 일 때 무엇을 안내할지(지갑 연결 / MetaMask 승인 / 잠시 후 재시도)는 {@code status} 로 가른다.
 *
 * <p>{@code chainId}·{@code escrowAddress} 는 프런트가 {@code setOperator(operator, true)} 를 보낼
 * 컨트랙트다. Backend 는 그 트랜잭션을 대신 보내지 않는다 — 공급자 MetaMask 가 직접 서명한다.
 */
public record OperatorStatusResponse(
        String workerId,
        String providerAddress,
        String operatorAddress,
        boolean approved,
        OperatorApproval status,
        Long chainId,
        String escrowAddress
) {
    public static OperatorStatusResponse of(Worker worker, OperatorApproval status,
                                            Long chainId, String escrowAddress) {
        return new OperatorStatusResponse(
                worker.getId(),
                worker.getOwner().getWalletAddress(),
                worker.getOperatorAddress(),
                status.isApproved(),
                status,
                chainId,
                escrowAddress);
    }
}
