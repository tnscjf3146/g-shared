package com.gshared.back.job.dto.request;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import tools.jackson.databind.JsonNode;

/**
 * Job 생성 요청.
 * 견적(predictedRuntimeSec·estimatedCost)과 GPU 선택은 서버가 Scheduler 로 계산한다 —
 * 클라이언트가 보내던 구조는 가격 조작이 가능해 제거했다.
 */
public record CreateJobRequest(

        @NotNull(message = "workloadId는 필수값입니다.")
        Long workloadId,            // workloads PK (V8 — 정수)

        @NotNull(message = "baseModelId는 필수값입니다.")
        Long baseModelId,           // base_models PK (V8 — 정수)

        @NotNull(message = "datasetId는 필수값입니다.")
        Long datasetId,

        @NotBlank(message = "trainingMode는 필수값입니다.")
        @Pattern(regexp = "AUTO|CUSTOM", message = "trainingMode는 AUTO 또는 CUSTOM이어야 합니다.")
        String trainingMode,

        // AUTO 일 때만: 학습 방향 (Scheduler 가 세부 설정을 확정)
        @Pattern(regexp = "SPEED|BALANCED|TRAINING", message = "trainingPriority는 SPEED/BALANCED/TRAINING 중 하나입니다.")
        String trainingPriority,

        // CUSTOM 일 때만: {epochs, batchSize, learningRate, maxLength}
        JsonNode trainingConfig,

        // ── 어댑터 (없으면 기존과 똑같이 동작한다) ────────────────────
        /** 어댑터를 얹고 시작하는가. null → false. true 면 파일 업로드 검증이 끝나야 /fund 가 열린다. */
        Boolean hasAdapter,

        /**
         * 결과 어댑터 이름 = 내려받을 파일 이름. 비어 오면 서버가 기본값을 붙인다.
         * 길이·문자 검사는 {@code AdapterName} 한 곳에서 한다 — 여기에 @Size 를 겹쳐 두면
         * 같은 실수(너무 긴 이름 / 못 쓰는 글자)에 서로 다른 에러 코드가 나간다.
         */
        String adapterName,

        /**
         * 이어서 학습할 완료 작업. 그 작업의 결과물(result.tar)이 이번 학습의 입력 어댑터가 되고,
         * 결과 버전이 하나 올라간다(v1 → v2 → v3). 따로 올릴 파일이 없다.
         */
        Long parentJobId,

        // ── GPU 선택 (없으면 기존과 똑같이 동작한다) ──────────────────
        /**
         * 추천 화면에서 수요자가 고른 GPU 등급({@code GET /gpus} · {@code POST /jobs/gpu-options} 의 id).
         * 비워 두면 Scheduler 의 1 순위를 쓴다.
         *
         * <p>워커가 아니라 등급인 이유는 화면이 고르는 단위가 그것이기 때문이다 — 사용자는
         * "누구의 PC"가 아니라 "어떤 그래픽카드"를 고르고, 같은 등급에 워커가 여럿일 수 있다.
         * 서버가 그중 순위가 가장 높은 워커를 배정한다.
         *
         * <p><b>선택일 뿐, 값이 아니다.</b> 예상 시간·비용·정산 지갑은 서버가 추천을 다시 받아
         * 계산한다 — 클라이언트가 보낸 숫자는 어느 것도 저장되지 않는다. 이 등급에 돌릴 수 있는
         * 워커가 지금 없으면 {@code SELECTED_WORKER_UNAVAILABLE} 이다.
         */
        Long gpuId
) {
}
