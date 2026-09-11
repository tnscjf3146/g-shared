package com.gshared.back.worker.dto.response;

/** GPU 벤치마크 갱신 응답 (규격 §6). */
public record UpdateBenchmarkResponse(
        String workerId,
        boolean updated
) {
}
