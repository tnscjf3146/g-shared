package com.gshared.back.worker.dto.request;

import jakarta.validation.constraints.NotBlank;

/**
 * 워커 재접속 확인 요청 (규격 §4). 저장해 둔 workerId·workerToken 이 아직 유효한지 묻는다.
 * 규격상 토큰은 헤더가 아니라 본문에 실린다.
 */
public record AuthenticateWorkerRequest(

        @NotBlank(message = "workerId는 필수값입니다.")
        String workerId,

        @NotBlank(message = "workerToken은 필수값입니다.")
        String workerToken
) {
}
