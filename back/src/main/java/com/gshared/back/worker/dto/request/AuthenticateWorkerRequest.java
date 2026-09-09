package com.gshared.back.worker.dto.request;

import jakarta.validation.constraints.NotBlank;

/**
 * 워커 재접속 확인 요청. 인증 수단은 헤더의 워커 토큰이고,
 * 본문의 workerId 는 "내가 이 워커라고 알고 있다"는 주장이다.
 * 서버는 토큰이 가리키는 워커와 이 주장이 일치하는지 확인한다.
 */
public record AuthenticateWorkerRequest(

        @NotBlank(message = "workerId는 필수값입니다.")
        String workerId
) {
}
