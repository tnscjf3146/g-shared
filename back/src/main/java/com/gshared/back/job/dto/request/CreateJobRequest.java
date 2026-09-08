package com.gshared.back.job.dto.request;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Positive;
import tools.jackson.databind.JsonNode;

/**
 * Job 생성 요청. 사용자가 견적 승인 후 호출.
 * trainingConfig 는 CUSTOM 일 때만 값이 있고(예: {epochs, batchSize, learningRate, maxLength}),
 * AUTO 면 null. JSON 객체 그대로 받아 서비스에서 문자열로 저장한다.
 */
public record CreateJobRequest(

        @NotNull(message = "workloadId는 필수값입니다.")
        Long workloadId,

        @NotNull(message = "baseModelId는 필수값입니다.")
        Long baseModelId,

        @NotNull(message = "datasetId는 필수값입니다.")
        Long datasetId,

        @NotNull(message = "gpuId는 필수값입니다.")
        Long gpuId,

        @NotBlank(message = "trainingMode는 필수값입니다.")
        @Pattern(regexp = "AUTO|CUSTOM", message = "trainingMode는 AUTO 또는 CUSTOM이어야 합니다.")
        String trainingMode,

        JsonNode trainingConfig,

        @NotNull(message = "predictedRuntimeSec는 필수값입니다.")
        @Positive(message = "predictedRuntimeSec는 양수여야 합니다.")
        Integer predictedRuntimeSec,

        @NotNull(message = "estimatedCost는 필수값입니다.")
        @Positive(message = "estimatedCost는 양수여야 합니다.")
        Long estimatedCost
) {
}
