package com.gshared.back.global.client.dto;

import java.util.List;

/**
 * POST /scheduler/recommend 요청.
 * Scheduler 는 모르는 필드·null·0 이하 값을 전부 422 로 거절하므로 규격을 정확히 지킨다.
 * minVramMb 는 Scheduler 가 아직 받지 않아 제외 (Feasibility Filter 추가 시 함께 추가).
 */
public record SchedulerRecommendRequest(
        String modelId,          // "qwen2.5-7b"
        String dockerImageId,    // "qlora-sft-v1"
        DatasetMeta dataset,
        SchedulerTrainingConfig trainingConfig,
        List<GpuCandidate> gpuCandidates
) {
    /** Dataset 메타 6개 — 전부 필수. */
    public record DatasetMeta(
            Long datasetSizeBytes,
            Integer sampleCount,
            Long totalTokens,
            Long effectiveTokens,
            Double avgSeqLength,
            Integer maxSeqLength
    ) {
    }

    /** GPU 후보 — null 필드가 있는 워커는 후보에 넣지 말 것 (422). */
    public record GpuCandidate(
            String workerId,
            String gpuModel,
            Integer vramMb,
            Double benchmarkTokensPerSec
    ) {
    }
}
