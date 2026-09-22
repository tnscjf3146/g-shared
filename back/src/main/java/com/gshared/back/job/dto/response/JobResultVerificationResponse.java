package com.gshared.back.job.dto.response;

import java.util.List;
import java.util.Map;

/**
 * GET /jobs/{jobId}/result-verification 응답 (Training_Result_Verification_API_Guide §19).
 *
 * <p>Runtime 이 결과 tar 안에 넣어 준 {@code training_metrics.json} 을 그대로 전달하는 껍데기다 —
 * Backend 는 LoRA weight 를 분석하지 않는다. 검증 데이터가 없는 legacy 결과는 404 가 아니라
 * {@code available=false + reason} 으로 답해 화면이 중립 빈 상태를 그리게 한다.
 */
public record JobResultVerificationResponse(
        boolean available,
        String reason,          // available=false 일 때만: LEGACY_RESULT · METRICS_NOT_AVAILABLE · VERIFICATION_PARSE_FAILED · RESULT_NOT_READY
        Training training,
        Adapter adapter,
        Integrity integrity
) {

    public record Training(
            Integer sampleCount,
            Integer totalSteps,
            Double finalLoss,
            Double actualRuntimeSec,
            List<LossPoint> lossHistory
    ) {
    }

    public record LossPoint(int step, double loss) {
    }

    public record Adapter(
            Integer tensorCount,
            Integer changedTensorCount,
            Integer nonZeroTensorCount,
            Integer nanCount,
            Integer infCount,
            Boolean valid,
            /* layer 번호("0"…) → module(q_proj…) → 학습 전후 weight 변화량 */
            Map<String, Map<String, Double>> layerModuleDeltaMagnitude
    ) {
    }

    public record Integrity(String resultHash) {
    }

    public static JobResultVerificationResponse unavailable(String reason) {
        return new JobResultVerificationResponse(false, reason, null, null, null);
    }
}
