package com.gshared.back.user.dto.request;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;

public record WalletChallengeRequest(

        @NotBlank(message = "지갑 주소는 필수값입니다.")
        String walletAddress,

        @NotNull(message = "네트워크(chainId)는 필수값입니다.")
        Long chainId
) {
}
