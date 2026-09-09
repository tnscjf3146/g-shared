package com.gshared.back.worker.dto.request;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;

/**
 * GPU 정보 갱신 요청. 그래픽카드 교체·드라이버 갱신·벤치마크 재측정 시 워커가 보낸다.
 *
 * <p>등록(register)과 달리 네 값 모두 필수다 — 이 값들은 AI Scheduler 가 실행시간을
 * 예측하는 입력이라, 비워 두면 스케줄러가 빈 값을 그대로 믿는다.
 */
public record UpdateWorkerGpuRequest(

        @NotBlank(message = "gpuModel은 필수값입니다.")
        String gpuModel,

        @NotNull(message = "vramMb는 필수값입니다.")
        @Positive(message = "vramMb는 양수여야 합니다.")
        Integer vramMb,

        @NotBlank(message = "driverVersion은 필수값입니다.")
        String driverVersion,

        @NotNull(message = "benchmarkTokensPerSec은 필수값입니다.")
        @Positive(message = "benchmarkTokensPerSec은 양수여야 합니다.")
        Double benchmarkTokensPerSec
) {
}
