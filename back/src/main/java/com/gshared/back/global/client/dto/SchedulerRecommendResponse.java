package com.gshared.back.global.client.dto;

import java.util.List;

/** POST /scheduler/recommend 응답. */
public record SchedulerRecommendResponse(
        String schedulerModelVersion,   // "runtime-xgb-v1" — 저장해야 나중에 예측 정확도 평가 가능
        String recommendedWorkerId,
        // AI 가 이 Job 의 실제 설정(모델·batch·길이)으로 계산한 필요 VRAM (Dynamic VRAM, 2026-09-21).
        // null 허용 — AI 구버전/배포 순서 차이면 없을 수 있고, 그때는 카탈로그 값(baseModel.requiredVramMb)으로 후퇴한다.
        Integer requiredVramMb,
        List<Ranking> rankings
) {
    public record Ranking(
            Integer rank,
            String workerId,
            Double predictedRuntimeSec
    ) {
    }
}
