package com.gshared.back.job.dto.response;

/**
 * Execution Context (규격 §11) — System ↔ Backend 의 핵심 Job 계약.
 * JOB_ASSIGN 은 "배정됐다"는 알림일 뿐이고, 실제로 무엇을 어떻게 돌릴지는 워커가 이걸 받아 안다.
 *
 * <p>jobId·escrowJobId·expectedPriceWei 는 숫자여도 문자열이다 — System/WebSocket 계약은 jobId 를
 * 문자열로 통일했고(§1.1), uint256 은 JS 정수 정밀도를 넘는다(§1.5).
 */
public record ExecutionContextResponse(
        String jobId,
        String assignedWorkerId,
        Model model,
        RuntimeImage runtimeImage,
        Dataset dataset,
        TrainingConfig trainingConfig,
        Escrow escrow
) {
    public record Model(
            Long modelId,
            String modelRef,        // "Qwen/Qwen2.5-7B"
            String modelRevision    // 고정 커밋 SHA (없으면 null)
    ) {
    }

    public record RuntimeImage(
            Long dockerImageId,
            String imageRef,        // "le3560/gshared-qlora-sft-runtime@sha256:..."
            String digest           // "sha256:..."
    ) {
    }

    public record Dataset(
            Long datasetId,
            String downloadUrl,     // 서명 URL — 만료 전에 받아야 한다
            String sha256,          // 64자리 hex (0x 없음) — 내려받은 파일 검증용
            Long datasetSizeBytes,
            Integer sampleCount,
            Long totalTokens,
            Long effectiveTokens,
            Double avgSeqLength,
            Integer maxSeqLength
    ) {
    }

    /** Runtime 이 받는 최종 numeric config — SPEED/BALANCED 같은 preset 이름이 아니다 (§15). */
    public record TrainingConfig(
            Integer epochs,
            Integer batchSize,
            Double learningRate,
            Integer maxLength
    ) {
    }

    /**
     * acceptJob 전 대조값 (§12). 워커는 온체인 Job 의 externalRef·client·provider·price·state 를
     * 이 값과 비교하고, 하나라도 다르면 JOB_REJECTED 로 답한다.
     */
    public record Escrow(
            Long chainId,
            String contractAddress,
            String escrowJobId,     // 온체인 uint256 jobId — 문자열
            String externalRef,     // bytes32 hex
            String client,          // 의뢰자 지갑 (예치한 주소)
            String provider,        // 공급자 정산 지갑 (workers.owner.walletAddress)
            Long expectedPriceToken,
            String expectedPriceWei,
            Integer expectedState   // FUNDED = 1
    ) {
    }
}
