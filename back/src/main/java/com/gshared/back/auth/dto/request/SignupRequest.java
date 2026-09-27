package com.gshared.back.auth.dto.request;

import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

public record SignupRequest(

        @Email(message = "이메일 형식이 아닙니다.")
        @NotBlank(message = "이메일은 필수값입니다.")
        String email,

        @NotBlank(message = "비밀번호는 필수값입니다.")
        @Size(min = 6, max = 20, message = "비밀번호는 6 ~ 20자입니다.")
        @Pattern(regexp = "^(?=.*[a-zA-Z])(?=.*\\d).+$", message = "비밀번호는 영문과 숫자를 포함해야 합니다.")
        String password,

        @NotBlank(message = "닉네임은 필수값입니다.")
        @Size(min = 2, max = 15, message = "닉네임은 2 ~ 15자 입니다.")
        @Pattern(regexp = "^[a-zA-Z0-9가-힣]+$", message = "닉네임은 영문, 숫자, 한글만 사용할 수 있습니다.")
        String nickname
) {
}
