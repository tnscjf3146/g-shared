package com.gshared.back.global.client.dto;

/**
 * POST /scheduler/training-config 요청 (AUTO 모드의 세부 설정을 Scheduler 가 확정).
 *
 * <p>dataset 은 recommend 의 {@link SchedulerRecommendRequest.DatasetMeta} 와 <b>다른 스키마</b>다.
 * Scheduler 의 {@code AutoTrainingConfigRequest.dataset} 은 5개 필드만 받고 {@code extra="forbid"} 라
 * {@code effectiveTokens} 가 섞이면 422 로 거절한다. recommend 쪽은 반대로 {@code effectiveTokens} 가 필수다.
 * 그래서 6필드 record 를 재사용하지 않고 여기서 따로 정의한다.
 */
public record SchedulerTrainingConfigRequest(
        DatasetMeta dataset,
        String mode      // SPEED / BALANCED / TRAINING
) {
    /** Scheduler {@code DatasetMetadata} — 5개 전부 필수, 그 외 필드 금지. */
    public record DatasetMeta(
            Long datasetSizeBytes,
            Integer sampleCount,
            Long totalTokens,
            Double avgSeqLength,
            Integer maxSeqLength
    ) {
        /** recommend 용 6필드에서 effectiveTokens 만 떼어낸다. */
        public static DatasetMeta from(SchedulerRecommendRequest.DatasetMeta meta) {
            return new DatasetMeta(
                    meta.datasetSizeBytes(), meta.sampleCount(), meta.totalTokens(),
                    meta.avgSeqLength(), meta.maxSeqLength());
        }
    }
}
