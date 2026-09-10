package com.gshared.back.job.dto.request;

import com.gshared.back.global.client.dto.SchedulerRecommendRequest;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;

/**
 * 아직 저장하지 않은 데이터셋의 분석 결과.
 *
 * <p>견적 단계에서는 파일을 올리기 전이라 {@code datasetId} 가 없다. 그래서 등록된 데이터셋을
 * 가리키는 대신, 프론트가 파일을 분석해 만든 수치를 그대로 받는다. 파일 자체는 오지 않는다 —
 * 견적에 필요한 것은 크기와 토큰 수뿐이고, 몇 GB 를 왕복시킬 이유가 없다.
 */
public record DatasetMetadataRequest(

        @NotNull(message = "sizeBytes는 필수값입니다.")
        @Positive(message = "sizeBytes는 양수여야 합니다.")
        Long sizeBytes,

        @NotNull(message = "sampleCount는 필수값입니다.")
        @Positive(message = "sampleCount는 양수여야 합니다.")
        Integer sampleCount,

        @NotNull(message = "totalTokens는 필수값입니다.")
        @Positive(message = "totalTokens는 양수여야 합니다.")
        Long totalTokens,

        @NotNull(message = "avgSeqLength는 필수값입니다.")
        @Positive(message = "avgSeqLength는 양수여야 합니다.")
        Double avgSeqLength,

        @NotNull(message = "maxSeqLength는 필수값입니다.")
        @Positive(message = "maxSeqLength는 양수여야 합니다.")
        Integer maxSeqLength
) {
    /**
     * Scheduler 규격으로 옮긴다.
     *
     * <p>ponytail: Scheduler 는 effectiveTokens(실제 계산되는 토큰)를 따로 요구하는데
     * 화면 분석은 그 값을 만들지 않는다. 지금은 totalTokens 를 그대로 쓴다 —
     * 패딩·잘림을 감안하지 않은 값이라 실제보다 짧게 잡힐 수 있고, 그만큼 견적이 낮아진다.
     * 등록된 데이터셋으로 주문할 때는 저장된 effectiveTokens 를 쓰므로 최종 금액은 그쪽이 기준이다.
     * 프론트 분석기가 effectiveTokens 를 내보내게 되면 이 자리만 교체한다.
     */
    public SchedulerRecommendRequest.DatasetMeta toSchedulerMeta() {
        return new SchedulerRecommendRequest.DatasetMeta(
                sizeBytes, sampleCount, totalTokens, totalTokens, avgSeqLength, maxSeqLength);
    }
}
