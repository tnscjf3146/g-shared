package com.gshared.back.worker.service;

import com.gshared.back.global.blockchain.EscrowVerification;
import com.gshared.back.worker.domain.OperatorApproval;
import com.gshared.back.worker.domain.Worker;
import com.gshared.back.worker.domain.WorkerAvailability;
import com.gshared.back.worker.domain.WorkerStatus;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

/**
 * 워커 한 대의 operator 승인·가용성을 체인에 물어 정한다.
 *
 * <p>주소의 출처는 둘뿐이고 섞이면 안 된다:
 * <pre>
 *   providerAddress = users.wallet_address   (정산 받는 지갑, setOperator 를 보내는 주체)
 *   operatorAddress = workers.operator_address (워커 PC 의 서명 키)
 * </pre>
 * 같은 사람이 의뢰자이자 공급자여도 승인은 여전히 {@code isOperator(providerWallet, operator)} 다 —
 * 지갑이 같다는 이유로 승인을 건너뛰지 않는다.
 *
 * <p>여기서 얻은 답은 화면용이다. 매칭({@code JobService})은 이 값을 믿지 않고 자기 시점에 체인에
 * 다시 묻는다 — 화면이 "승인됨"이라고 보여 준 것은 UX 이고, 배정 직전의 온체인 확인이 권위다.
 */
@Component
@RequiredArgsConstructor
public class OperatorApprovalChecker {

    private final EscrowVerification escrow;

    public OperatorApproval approvalOf(Worker worker) {
        String providerWallet = worker.getOwner().getWalletAddress();
        String operatorAddress = worker.getOperatorAddress();
        if (providerWallet == null || providerWallet.isBlank()) {
            return OperatorApproval.PROVIDER_WALLET_MISSING;
        }
        if (operatorAddress == null || operatorAddress.isBlank()) {
            return OperatorApproval.OPERATOR_MISSING;
        }
        return switch (escrow.operatorApproved(providerWallet, operatorAddress)) {
            case CONFIRMED -> OperatorApproval.APPROVED;
            case CONTRADICTED -> OperatorApproval.NOT_APPROVED;
            case UNKNOWN -> OperatorApproval.UNVERIFIED;
        };
    }

    /**
     * 가용성. OFFLINE·작업 중이면 체인에 묻지 않는다 — 어차피 후보가 아니고, 내 워커 목록을 열 때마다
     * 꺼진 PC 수만큼 RPC 를 보낼 이유가 없다.
     */
    public WorkerAvailability availabilityOf(Worker worker) {
        if (worker.getStatus() == WorkerStatus.OFFLINE
                || worker.getStatus() == WorkerStatus.RUNNING || worker.getCurrentJobId() != null) {
            return WorkerAvailability.of(worker.getStatus(), worker.getCurrentJobId(),
                    OperatorApproval.UNVERIFIED, escrow.isConfigured());
        }
        return WorkerAvailability.of(worker.getStatus(), worker.getCurrentJobId(),
                approvalOf(worker), escrow.isConfigured());
    }
}
