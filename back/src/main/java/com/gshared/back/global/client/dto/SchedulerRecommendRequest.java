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
     * 값은 {@link com.gshared.back.workload.domain.BaseModel#requiredVramMb()} (모델 하한, 없으면 workloads.min_vram_mb) —
     * Backend 는 이 값으로 워커를 직접 거르지 않는다 (같은 판단이 두 곳에 살면 갈린다).
     */
    public record Requirements(
            Integer minVramMb    // 필수, 양수. gpuCandidates[].vramMb 와 같은 단위(MiB)
    ) {
    }

    /**
     * GPU 후보 — benchmarkPeakVramMb 를 뺀 필드에 null 이 있는 워커는 후보에 넣지 말 것 (422).
     * benchmarkPeakVramMb 만 선택이다: 구버전 exe 로 등록한 워커는 값이 없고, Scheduler 가 null 을 허용한다.
     */
    public record GpuCandidate(
            String workerId,
            String gpuModel,
            Integer vramMb,                 // 카드 총량 (드라이버 보고값)
            Double benchmarkTokensPerSec,
            Integer benchmarkPeakVramMb     // 벤치마크 중 실제로 쓴 최고치 — 없으면 null
    ) {
    }
}
