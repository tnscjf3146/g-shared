package com.gshared.back.global.client.dto;

import java.util.List;

/** POST /scheduler/recommend 응답. */
public record SchedulerRecommendResponse(
        String schedulerModelVersion,   // "runtime-xgb-v1" — 저장해야 나중에 예측 정확도 평가 가능
        String recommendedWorkerId,
        List<Ranking> rankings
) {
    public record Ranking(
            Integer rank,
            String workerId,
            Double predictedRuntimeSec
    ) {
    }
}
