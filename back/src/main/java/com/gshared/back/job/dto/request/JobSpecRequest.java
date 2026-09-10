package com.gshared.back.job.dto.request;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import tools.jackson.databind.JsonNode;

/**
 * 작업 명세 — 아직 주문이 아니라 "이런 걸 시키면 얼마인가"를 묻는 몸통.
 * GPU 후보 조회와 견적이 공통으로 받는다.
 */
public record JobSpecRequest(

        @NotNull(message = "workloadId는 필수값입니다.")
        Long workloadId,

        @NotNull(message = "baseModelId는 필수값입니다.")
        Long baseModelId,

        @NotNull(message = "datasetMetadata는 필수값입니다.")
        @Valid
        DatasetMetadataRequest datasetMetadata,

        @NotBlank(message = "trainingMode는 필수값입니다.")
        @Pattern(regexp = "AUTO|CUSTOM", message = "trainingMode는 AUTO 또는 CUSTOM이어야 합니다.")
        String trainingMode,

        // AUTO 일 때만: 학습 방향. 화면은 autoMode 라는 이름으로 보낸다.
        @Pattern(regexp = "SPEED|BALANCED|TRAINING", message = "autoMode는 SPEED/BALANCED/TRAINING 중 하나입니다.")
        String autoMode,

        // CUSTOM 일 때만: {epochs, batchSize, learningRate, maxLength}
        JsonNode trainingConfig,

        // 화면 흐름용 표시값. 지금 견적 계산에는 쓰이지 않지만, 받아 두지 않으면
        // 프론트가 보낸 필드가 조용히 사라져 나중에 왜 반영이 안 되는지 찾기 어려워진다.
        Boolean hasAdapter,
        Boolean ownModel
) {
}
