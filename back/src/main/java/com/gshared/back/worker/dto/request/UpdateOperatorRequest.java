package com.gshared.back.worker.dto.request;

import jakarta.validation.constraints.NotBlank;

/** operator 주소 변경 요청 (규격 §5). 워커가 서명 키를 새로 만들었을 때 보낸다. */
public record UpdateOperatorRequest(

        @NotBlank(message = "operatorAddress는 필수값입니다.")
        String operatorAddress
) {
}
