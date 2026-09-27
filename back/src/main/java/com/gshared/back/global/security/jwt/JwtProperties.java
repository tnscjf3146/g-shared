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
        Duration refreshExpiration,

        /**
         * 회전 직후 옛 refresh 토큰을 얼마나 더 받아 줄지.
         *
         * <p>재발급 응답이 오기 전에 화면이 넘어가면 브라우저가 새 쿠키를 못 받는다. 그 상태로 다음 요청이
         * 옛 토큰을 들고 오는 것은 도둑질이 아니라 흔한 경합이라, 이 시간 동안은 받아 준다(수정요청서 BE-08).
         * 짧게 둘수록 안전하고 길게 둘수록 관대하다 — 느린 네트워크에서 한 번의 왕복이면 충분한 정도로.
         */
        @NotNull(message = "jwt.refresh-grace 설정이 필요합니다.")
        Duration refreshGrace
) {
    /**
     * 유예를 0(또는 음수)으로 두면 Redis 회전 스크립트가 {@code SET ... EX 0} 을 실행해 오류를 내고,
     * 재발급이 통째로 500 이 된다 — 기동 때 막는다(보안 리뷰 지적).
     */
    public JwtProperties {
        if (refreshGrace != null && (refreshGrace.isZero() || refreshGrace.isNegative())) {
            throw new IllegalArgumentException("jwt.refresh-grace 는 0 보다 커야 합니다. (현재: " + refreshGrace + ")");
        }
    }
}
