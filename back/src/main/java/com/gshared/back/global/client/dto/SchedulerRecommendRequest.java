package com.gshared.back.global.client.dto;

import java.util.List;

/**
 * POST /scheduler/recommend 요청.
 * Scheduler 는 모르는 필드·null·0 이하 값을 전부 422 로 거절하므로 규격을 정확히 지킨다.
 */
public record SchedulerRecommendRequest(
        Long modelId,            // base_models PK (Scheduler feature schema v2 — 정수)
        Long dockerImageId,      // workloads PK
        DatasetMeta dataset,
        SchedulerTrainingConfig trainingConfig,
        Requirements requirements,
        List<GpuCandidate> gpuCandidates
) {
    /** Dataset 메타 6개 — 전부 필수. (training-config 는 effectiveTokens 를 받지 않는다 — 그쪽 DTO 는 따로) */
    public record DatasetMeta(
            Long datasetSizeBytes,
            Integer sampleCount,
            Long totalTokens,
            Long effectiveTokens,
            Double avgSeqLength,
            Integer maxSeqLength
    ) {
    }

    /**
     * Job 을 돌리는 데 필요한 기술 조건. Scheduler 는 DB 를 보지 않으므로 카탈로그를 가진 Backend 가 실어 보낸다.
     * 값은 workloads.min_vram_mb — Backend 는 이 값으로 워커를 직접 거르지 않는다 (같은 판단이 두 곳에 살면 갈린다).
     */
    public record Requirements(
            Integer minVramMb    // 필수, 양수. gpuCandidates[].vramMb 와 같은 단위(MiB)
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
