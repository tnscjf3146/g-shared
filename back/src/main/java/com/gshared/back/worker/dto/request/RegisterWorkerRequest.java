package com.gshared.back.worker.dto.request;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;

/**
 * 공급자 PC 등록 요청 (규격 §3). Worker 클라이언트가 자기 환경을 조사·측정해 채워 보낸다.
 *
 * <p>operatorAddress 는 워커 PC 가 만든 서명 키의 <b>공개 주소</b>다. 개인키는 절대 오지 않는다.
 * gpu·benchmark 는 AI Scheduler 가 실행시간을 예측하는 입력이라 전부 필수·양수다 —
 * 비워 두면 스케줄러가 빈 값을 그대로 믿는다. 예외는 benchmark.peakVramMb 하나 — 그 필드 주석 참고.
 */
public record RegisterWorkerRequest(

        @NotBlank(message = "operatorAddress는 필수값입니다.")
        String operatorAddress,

        @NotBlank(message = "clientVersion은 필수값입니다.")
        String clientVersion,

        @NotNull(message = "system은 필수값입니다.")
        @Valid
        SystemInfo system,

        @NotNull(message = "gpu는 필수값입니다.")
        @Valid
        GpuInfo gpu,

        @NotNull(message = "benchmark는 필수값입니다.")
        @Valid
        BenchmarkInfo benchmark
) {
    public record SystemInfo(
            @NotBlank(message = "system.os는 필수값입니다.")
            String os,

            @NotNull(message = "system.wslAvailable은 필수값입니다.")
            Boolean wslAvailable
    ) {
    }

    public record GpuInfo(
            @NotBlank(message = "gpu.gpuModel은 필수값입니다.")
            String gpuModel,

            @NotNull(message = "gpu.vramMb는 필수값입니다.")
            @Positive(message = "gpu.vramMb는 양수여야 합니다.")
            Integer vramMb
    ) {
    }

    public record BenchmarkInfo(
            @NotNull(message = "benchmark.benchmarkTokensPerSec은 필수값입니다.")
            @Positive(message = "benchmark.benchmarkTokensPerSec은 양수여야 합니다.")
            Double benchmarkTokensPerSec,

            /**
             * 벤치마크 중 실제로 쓴 VRAM 최고치(MiB). 선택이다 — 구버전 G-shared.exe 는 이 필드를 보내지 않는데,
             * 필수로 막으면 그 공급자의 등록이 400 으로 끊겨 PC 가 일을 못 받는다. 없으면 null 로 저장한다.
             */
            @Positive(message = "benchmark.peakVramMb는 양수여야 합니다.")
            Integer peakVramMb,

            @NotBlank(message = "benchmark.benchmarkVersion은 필수값입니다.")
            String benchmarkVersion
    ) {
    }
}
