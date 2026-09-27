package com.gshared.back.job.service;

import com.gshared.back.global.blockchain.EscrowChainClient.OnChainJob;
import com.gshared.back.global.blockchain.EscrowJobState;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.math.BigInteger;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 대조기의 취소 원인 귀속(-218) — 순수 판정만 검증한다.
 * PROVIDER_TIMEOUT 은 "워커 손에 있던 작업이 deadline 을 넘긴 경우"에만 추정한다:
 * 사용자 온체인 cancelJob 은 배정 전(FUNDED)에서만 가능하므로, 배정 전 취소는
 * 아무리 늦게 발견해도 공급자 무응답으로 바뀌면 안 된다.
 */
class EscrowReconcilerCancelReasonTest {

    private static OnChainJob cancelled(long deadline) {
        return new OnChainJob("0xclient", EscrowJobState.CANCELLED, deadline,
                "0xprovider", "0xoperator", BigInteger.TEN, null);
    }

    @Test
    @DisplayName("워커 보유 + deadline 경과 → PROVIDER_TIMEOUT (키퍼 시한 정산의 인계)")
    void workerHeldAndExpiredIsProviderTimeout() {
        assertThat(EscrowReconciler.cancelReasonCode(cancelled(1_000L), true, Optional.of(2_000L)))
                .isEqualTo("PROVIDER_TIMEOUT");
    }

    @Test
    @DisplayName("배정 전(워커 없음) 취소는 deadline 이 지나 있어도 PROVIDER_TIMEOUT 이 아니다 — 사용자 취소 늦발견 보호")
    void unassignedCancelIsNeverProviderTimeout() {
        assertThat(EscrowReconciler.cancelReasonCode(cancelled(1_000L), false, Optional.of(2_000L)))
                .isEqualTo("SYSTEM_RECONCILED");
    }

    @Test
    @DisplayName("deadline 이 안 지났으면 시스템 정리다")
    void notExpiredIsSystemReconciled() {
        assertThat(EscrowReconciler.cancelReasonCode(cancelled(3_000L), true, Optional.of(2_000L)))
                .isEqualTo("SYSTEM_RECONCILED");
    }

    @Test
    @DisplayName("체인 시각을 못 읽거나 deadline 을 모르면 추정하지 않는다")
    void unknownClockOrDeadlineFallsBack() {
        assertThat(EscrowReconciler.cancelReasonCode(cancelled(1_000L), true, Optional.empty()))
                .isEqualTo("SYSTEM_RECONCILED");
        assertThat(EscrowReconciler.cancelReasonCode(cancelled(0L), true, Optional.of(2_000L)))
                .isEqualTo("SYSTEM_RECONCILED");
    }
}
