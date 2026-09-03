package com.gshared.back.global.security.jwt;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.validation.annotation.Validated;

import java.time.Duration;

@Validated
@ConfigurationProperties(prefix = "jwt")
public record JwtProperties(

        @NotBlank(message = "jwt.secret 설정이 필요합니다.")
        @Size(min = 32, message = "jwt.secret은 최소 32자여야 합니다. (HS256 = 256bit)")
        String secret,

        @NotNull(message = "jwt.access-expiration 설정이 필요합니다.")
        Duration accessExpiration,

        @NotNull(message = "jwt.refresh-expiration 설정이 필요합니다.")
        Duration refreshExpiration
) {
}
