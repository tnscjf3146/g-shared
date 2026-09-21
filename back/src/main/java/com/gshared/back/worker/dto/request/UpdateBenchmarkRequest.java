package com.gshared.back.worker.dto.request;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;

/**
 * GPU 벤치마크 갱신 요청 (규격 §6). 그래픽카드 교체·벤치마크 재측정 시 워커가 보낸다.
 *
 * <p>peakVramMb 를 뺀 네 값은 모두 필수다 — AI Scheduler 가 실행시간을 예측하는 입력이라,
 * 비워 두면 스케줄러가 빈 값을 그대로 믿는다.
 * benchmarkTokensPerSec 은 실제 Job 속도가 아니라 표준화된 합성 벤치마크 결과다.
 */
public record UpdateBenchmarkRequest(

        @NotBlank(message = "gpuModel은 필수값입니다.")
        String gpuModel,

        @NotNull(message = "vramMb는 필수값입니다.")
        @Positive(message = "vramMb는 양수여야 합니다.")
        Integer vramMb,

        @NotNull(message = "benchmarkTokensPerSec은 필수값입니다.")
        @Positive(message = "benchmarkTokensPerSec은 양수여야 합니다.")
        Double benchmarkTokensPerSec,

        /** 벤치마크 중 실제로 쓴 VRAM 최고치(MiB). 선택 — 구버전 exe 의 갱신이 400 으로 막히면 그 PC 는 일을 못 받는다. */
        @Positive(message = "peakVramMb는 양수여야 합니다.")
        Integer peakVramMb,

        @NotBlank(message = "benchmarkVersion은 필수값입니다.")
        String benchmarkVersion
) {
}
