package com.gshared.back.job.dto.request;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import tools.jackson.databind.JsonNode;

/**
 * Job 생성 요청.
 * 견적(predictedRuntimeSec·estimatedCost)과 GPU 선택은 서버가 Scheduler 로 계산한다 —
 * 클라이언트가 보내던 구조는 가격 조작이 가능해 제거했다.
 */
public record CreateJobRequest(

        @NotBlank(message = "workloadId는 필수값입니다.")
        String workloadId,          // "qlora-sft-v1"

        @NotBlank(message = "baseModelId는 필수값입니다.")
        String baseModelId,         // "qwen2.5-7b"

        @NotNull(message = "datasetId는 필수값입니다.")
        Long datasetId,

        @NotBlank(message = "trainingMode는 필수값입니다.")
        @Pattern(regexp = "AUTO|CUSTOM", message = "trainingMode는 AUTO 또는 CUSTOM이어야 합니다.")
        String trainingMode,

        // AUTO 일 때만: 학습 방향 (Scheduler 가 세부 설정을 확정)
        @Pattern(regexp = "SPEED|BALANCED|TRAINING", message = "trainingPriority는 SPEED/BALANCED/TRAINING 중 하나입니다.")
        String trainingPriority,

        // CUSTOM 일 때만: {epochs, batchSize, learningRate, maxLength}
        JsonNode trainingConfig
) {
}
