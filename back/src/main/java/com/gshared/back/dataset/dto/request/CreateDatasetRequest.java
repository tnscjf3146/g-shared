package com.gshared.back.dataset.dto.request;

import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;

/**
 * Dataset 메타데이터 등록 요청.
 * Scheduler 입력 6개는 전부 필수(하나라도 없으면 예측 불가 → 422).
 * Frontend 가 업로드 시 분석해서 보낸다.
 */
public record CreateDatasetRequest(

        @NotNull(message = "sizeBytes는 필수값입니다.")
        @Positive(message = "sizeBytes는 양수여야 합니다.")
        Long sizeBytes,

        @NotNull(message = "sampleCount는 필수값입니다.")
        @Positive(message = "sampleCount는 양수여야 합니다.")
        Integer sampleCount,

        @NotNull(message = "totalTokens는 필수값입니다.")
        @Positive(message = "totalTokens는 양수여야 합니다.")
        Long totalTokens,

        @NotNull(message = "effectiveTokens는 필수값입니다.")
        @Positive(message = "effectiveTokens는 양수여야 합니다.")
        Long effectiveTokens,

        @NotNull(message = "avgSeqLength는 필수값입니다.")
        @Positive(message = "avgSeqLength는 양수여야 합니다.")
        Double avgSeqLength,

        @NotNull(message = "maxSeqLength는 필수값입니다.")
        @Positive(message = "maxSeqLength는 양수여야 합니다.")
        Integer maxSeqLength,

        @Size(max = 66, message = "dataHash는 66자 이하입니다.")
        String dataHash,

        @Size(max = 512, message = "storageUri는 512자 이하입니다.")
        String storageUri
) {
}
