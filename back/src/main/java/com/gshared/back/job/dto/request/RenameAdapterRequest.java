package com.gshared.back.job.dto.request;

import jakarta.validation.constraints.NotBlank;

/**
 * 결과 어댑터 이름 변경. 이름은 그대로 내려받을 파일 이름이 되므로 경로 구분자·제어문자를 막는다.
 * 파일 자체도 계보(parentJobId)도 건드리지 않아 상태 제한이 없다 — 완료 뒤 받기 직전에 바꾸는 것이 주 사용처다.
 */
public record RenameAdapterRequest(

        // 길이·문자 검사는 AdapterName 이 한다 — 여기서는 "보내기는 했는가"만 본다
        @NotBlank(message = "adapterName은 필수값입니다.")
        String adapterName
) {
}
