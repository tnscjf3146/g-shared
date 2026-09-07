package com.gshared.back.global.wallet;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.validation.annotation.Validated;

import java.time.Duration;

/**
 * 지갑 연결 설정.
 * domain 과 uri 는 서명 원문에 그대로 들어가 사용자가 MetaMask 팝업에서 읽게 되므로
 * 요청 본문이 아니라 서버가 신뢰하는 설정값에서만 가져온다.
 */
@Validated
@ConfigurationProperties(prefix = "wallet-auth")
public record WalletAuthProperties(

        @NotBlank(message = "wallet-auth.domain 설정이 필요합니다.")
        String domain,

        @NotBlank(message = "wallet-auth.uri 설정이 필요합니다.")
        String uri,

        @NotNull(message = "wallet-auth.chain-id 설정이 필요합니다.")
        @Positive(message = "wallet-auth.chain-id는 양수여야 합니다.")
        Long chainId,

        @NotNull(message = "wallet-auth.challenge-expiration 설정이 필요합니다.")
        Duration challengeExpiration
) {
}
