package com.gshared.back.job.dto.request;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import tools.jackson.databind.JsonNode;

/**
 * 견적 요청 = 작업 명세 + 사용자가 고른 GPU 등급.
 *
 * <p>GPU 를 고른 뒤에야 시간과 비용이 정해지므로 명세만으로는 견적이 나오지 않는다.
 *
 * <p>명세를 중첩 객체로 두지 않고 펼쳐 받는 것은 화면이 그렇게 보내기 때문이다 —
 * GPU 후보를 물을 때 쓰던 몸통에 고른 등급 하나만 더해 그대로 보낸다.
 */
public record EstimateJobRequest(

        @NotNull(message = "gpuId는 필수값입니다.")
        Long gpuId,

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

        @Pattern(regexp = "SPEED|BALANCED|TRAINING", message = "autoMode는 SPEED/BALANCED/TRAINING 중 하나입니다.")
        String autoMode,

        JsonNode trainingConfig,

        Boolean hasAdapter,
        Boolean ownModel
) {
    /** 명세 부분만 떼어 낸다 — GPU 후보 조회와 같은 코드를 태우기 위해서다. */
    public JobSpecRequest spec() {
        return new JobSpecRequest(workloadId, baseModelId, datasetMetadata,
                trainingMode, autoMode, trainingConfig, hasAdapter, ownModel);
    }
}
