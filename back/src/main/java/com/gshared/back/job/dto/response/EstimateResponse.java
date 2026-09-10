package com.gshared.back.job.dto.response;

import com.gshared.back.global.client.dto.SchedulerTrainingConfig;

/**
 * 견적서. 사용자가 주문 버튼을 누르기 전에 보는 마지막 숫자다.
 *
 * <p>{@code trainingConfig} 를 함께 돌려주는 이유는 AUTO 일 때 Scheduler 가 정한 값을
 * 사용자가 확인해야 하기 때문이다 — 무엇으로 학습되는지 모른 채 금액만 보고 결정하게 두지 않는다.
 */
public record EstimateResponse(
        SchedulerTrainingConfig trainingConfig,
        double predictedRuntimeSec,
        long estimatedCost
) {
}
