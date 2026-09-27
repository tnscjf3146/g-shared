package com.gshared.back.job.dto.response;

/**
 * GPU 등급 하나에 대한 적합도 판정.
 *
 * <p>{@code fit} 은 "고를 수 있는가"가 아니라 "골랐을 때 어떻게 되는가"를 말한다.
 * INSUFFICIENT 도 목록에 남기는 것은, 왜 못 쓰는지 보여 주는 편이 아예 안 보이는 것보다
 * 낫기 때문이다(사용자는 자기 데이터셋이 큰 줄 모른다).
 */
public record GpuOptionResponse(
        long id,
        String model,
        int vramMb,
        long hourlyPrice,
        Fit fit,
        int requiredVramMb,
        String reason,
        int availableWorkers
) {
    public enum Fit {
        RECOMMENDED,   // Scheduler 가 1순위로 고른 등급
        OK,            // 여유 있게 감당
        TIGHT,         // 되긴 하지만 여유가 적다
        INSUFFICIENT   // VRAM 부족 — 이 등급으로는 실행 불가
    }
}
