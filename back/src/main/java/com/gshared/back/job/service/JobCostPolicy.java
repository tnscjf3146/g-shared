package com.gshared.back.job.service;

/**
 * 과금 공식이 있는 단 한 곳.
 *
 * <p>견적 화면(estimate)과 실제 주문(create)이 서로 다른 금액을 말하면 사용자는 어느 쪽도
 * 믿을 수 없다. 두 경로가 같은 식을 쓰도록 여기로 모았다.
 *
 * <p>ponytail: 과금 정책 미정 — 1만 토큰당 1 TOKEN 임시 공식. 정책이 확정되면 이 클래스만 교체한다.
 */
public final class JobCostPolicy {

    private static final long TOKENS_PER_CHARGE = 10_000L;

    private JobCostPolicy() {
    }

    /**
     * 예상 비용(TOKEN). 과금 기준은 실제로 계산되는 토큰 × 에폭 수다.
     * 아주 작은 작업도 0 원이 되지 않게 최소 1 TOKEN 을 둔다 — 공짜로 남의 GPU 를 쓰게 두지 않는다.
     */
    public static long estimate(long effectiveTokens, int epochs) {
        long billedTokens = effectiveTokens * epochs;
        return Math.max(1, billedTokens / TOKENS_PER_CHARGE);
    }
}
