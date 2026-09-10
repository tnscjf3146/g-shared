package com.gshared.back.global.client.dto;

/** POST /scheduler/training-config 요청 (AUTO 모드의 세부 설정을 Scheduler 가 확정). */
public record SchedulerTrainingConfigRequest(
        SchedulerRecommendRequest.DatasetMeta dataset,
        String mode      // SPEED / BALANCED / TRAINING
) {
}
