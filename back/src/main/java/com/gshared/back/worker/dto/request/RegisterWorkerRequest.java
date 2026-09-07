package com.gshared.back.worker.dto.request;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Positive;

/**
 * 공급자 PC 등록 요청. 하드웨어 스펙 스냅샷.
 * (Worker 클라이언트가 자기 환경을 조사해 채워 보낸다)
 */
public record RegisterWorkerRequest(

        @NotBlank(message = "deviceName은 필수값입니다.")
        String deviceName,

        String osVersion,

        @NotBlank(message = "gpuModel은 필수값입니다.")
        String gpuModel,

        @Positive(message = "vramMb는 양수여야 합니다.")
        Integer vramMb,

        String driverVersion,

        Double benchmarkTokensPerSec
) {
}
