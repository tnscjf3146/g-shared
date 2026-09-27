package com.gshared.back.user.dto.request;

import jakarta.validation.constraints.NotBlank;

/**
 * 지갑 주소를 다시 받지 않는다. challenge 발급 때 저장해둔 주소만 신뢰한다.
 */
public record WalletLinkRequest(

        @NotBlank(message = "challengeId는 필수값입니다.")
        String challengeId,

        @NotBlank(message = "서명은 필수값입니다.")
        String signature
) {
}
