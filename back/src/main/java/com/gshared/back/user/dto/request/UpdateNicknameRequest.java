package com.gshared.back.user.dto.request;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

/** 닉네임 변경. 규칙은 회원가입(SignupRequest)과 같아야 한다 — 가입 때 되던 이름이 변경 때 거절되면 안 된다. */
public record UpdateNicknameRequest(

        @NotBlank(message = "닉네임은 필수값입니다.")
        @Size(min = 2, max = 15, message = "닉네임은 2 ~ 15자 입니다.")
        @Pattern(regexp = "^[a-zA-Z0-9가-힣]+$", message = "닉네임은 영문, 숫자, 한글만 사용할 수 있습니다.")
        String nickname
) {
}
