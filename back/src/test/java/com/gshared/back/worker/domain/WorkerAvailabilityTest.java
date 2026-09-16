package com.gshared.back.worker.domain;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * ONLINE != AVAILABLE. 연결 상태에 작업·지갑·승인을 겹쳐 "지금 Job 을 받을 수 있는가"를 정한다.
 */
class WorkerAvailabilityTest {

    @Test
    @DisplayName("ONLINE + 미배정 + 승인 → AVAILABLE")
    void available() {
        assertThat(WorkerAvailability.of(WorkerStatus.ONLINE, null, OperatorApproval.APPROVED, true))
                .isEqualTo(WorkerAvailability.AVAILABLE);
    }

    @Test
    @DisplayName("ONLINE + 미배정 + 미승인 → OPERATOR_APPROVAL_REQUIRED")
    void approvalRequired() {
        assertThat(WorkerAvailability.of(WorkerStatus.ONLINE, null, OperatorApproval.NOT_APPROVED, true))
                .isEqualTo(WorkerAvailability.OPERATOR_APPROVAL_REQUIRED);
        // operator 주소 자체가 없어도 공급자가 할 일은 같다 — 주소를 넣고 승인하는 것
        assertThat(WorkerAvailability.of(WorkerStatus.ONLINE, null, OperatorApproval.OPERATOR_MISSING, true))
                .isEqualTo(WorkerAvailability.OPERATOR_APPROVAL_REQUIRED);
    }

    @Test
    @DisplayName("지갑이 없으면 승인 이전 문제 — WALLET_REQUIRED")
    void walletRequired() {
        assertThat(WorkerAvailability.of(WorkerStatus.ONLINE, null, OperatorApproval.PROVIDER_WALLET_MISSING, true))
                .isEqualTo(WorkerAvailability.WALLET_REQUIRED);
    }

    @ParameterizedTest(name = "status={0}, currentJobId={1} → {2}")
    @CsvSource({
            "OFFLINE, ,   OFFLINE",
            "RUNNING, 7,  BUSY",
            "ONLINE,  7,  BUSY",   // finishJob 전 레이스 — 작업 id 가 남아 있으면 아직 BUSY 다
    })
    @DisplayName("OFFLINE·작업 중이면 승인 여부와 무관하게 후보가 아니다")
    void connectionAndJobComeFirst(WorkerStatus status, Long currentJobId, WorkerAvailability expected) {
        assertThat(WorkerAvailability.of(status, currentJobId, OperatorApproval.APPROVED, true))
                .isEqualTo(expected);
    }

    @Test
    @DisplayName("확인 못 함: 체인이 설정돼 있으면 CHAIN_VERIFICATION_FAILED, 아니면 확인할 것이 없으니 AVAILABLE")
    void unverifiedDependsOnChainConfig() {
        assertThat(WorkerAvailability.of(WorkerStatus.ONLINE, null, OperatorApproval.UNVERIFIED, true))
                .isEqualTo(WorkerAvailability.CHAIN_VERIFICATION_FAILED);
        assertThat(WorkerAvailability.of(WorkerStatus.ONLINE, null, OperatorApproval.UNVERIFIED, false))
                .isEqualTo(WorkerAvailability.AVAILABLE);
    }
}
