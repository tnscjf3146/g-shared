package com.gshared.back.worker.dto.response;

import com.gshared.back.worker.domain.GpuGrade;

/**
 * GPU 등급 한 줄. 사용자가 작업 생성 화면에서 고르는 단위다.
 */
public record GpuResponse(
        long id,
        String model,
        int vramMb,
        long hourlyPrice
) {
    public static GpuResponse from(GpuGrade grade) {
        return new GpuResponse(grade.id(), grade.model(), grade.vramMb(), hourlyPrice(grade));
    }

    /**
     * 시간당 단가.
     *
     * <p>ponytail: 과금 정책 미정 — 지금 요금은 시간이 아니라 토큰으로 매긴다
     * ({@code JobService.estimateCost}). 시간당 단가에 대응하는 값이 없어 0 을 내보낸다.
     * 정책이 정해지면 이 메서드만 교체한다. 실제 청구는 견적(estimate)의 estimatedCost 로
     * 나가므로, 이 값이 0 이어도 금액이 틀리게 계산되지는 않는다.
     */
    private static long hourlyPrice(GpuGrade grade) {
        return 0L;
    }
}
