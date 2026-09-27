package com.gshared.back.auth.service;

import com.gshared.back.auth.exception.AuthErrorCode;
import com.gshared.back.global.exception.BusinessException;
import com.gshared.back.global.security.jwt.JwtProperties;
import com.gshared.back.global.security.jwt.JwtProvider;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import jakarta.servlet.http.HttpServletResponse;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.mock.web.MockHttpServletResponse;

import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;
import java.util.Date;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * 패밀리(fid) 도입 **이전에** 발급된 refresh 토큰이 어떻게 처리되는지 — 배포 순간의 이야기다(수정요청서 BE-08).
 *
 * <p>배포 직후, 브라우저에는 fid 없는 쿠키가 남아 있다. 그 토큰은 어느 세션인지 특정할 수 없어
 * 임의로 묶으면 남의 세션을 건드릴 수 있다. 그래서 무효로 본다 — 사용자는 한 번 다시 로그인하면 되고,
 * 그 뒤로는 새 규칙을 따른다. 여기서 확인하는 것은 "조용히 통과되지 않는다" 와 "500 이 아니라 401 이다" 다.
 */
@SpringBootTest
class LegacyRefreshTokenTest {

    @Autowired
    private AuthService authService;
    @Autowired
    private JwtProvider jwtProvider;
    @Autowired
    private JwtProperties jwtProperties;

    /** fid 클레임이 없는 옛 형식의 refresh 토큰 — 서명은 진짜다(위조가 아니라 "구버전" 이라는 점이 중요) */
    private String legacyRefreshToken(long userId) {
        SecretKey key = Keys.hmacShaKeyFor(jwtProperties.secret().getBytes(StandardCharsets.UTF_8));
        Date now = new Date();
        return Jwts.builder()
                .id(UUID.randomUUID().toString())
                .subject(String.valueOf(userId))
                .claim("tokenType", "REFRESH")
                .issuedAt(now)
                .expiration(new Date(now.getTime() + jwtProperties.refreshExpiration().toMillis()))
                .signWith(key)
                .compact();
    }

    @Test
    @DisplayName("fid 없는 구버전 토큰으로 재발급하면 401 INVALID_TOKEN — 조용히 통과하지 않는다")
    void legacyTokenIsRejected() {
        HttpServletResponse response = new MockHttpServletResponse();

        assertThatThrownBy(() -> authService.reissue(legacyRefreshToken(90_002L), response))
                .isInstanceOf(BusinessException.class)
                .extracting(e -> ((BusinessException) e).getErrorCode())
                .isEqualTo(AuthErrorCode.INVALID_TOKEN);
    }

    @Test
    @DisplayName("쿠키가 아예 없어도 401 INVALID_TOKEN (서버 오류가 아니다)")
    void missingCookieIsRejected() {
        HttpServletResponse response = new MockHttpServletResponse();

        assertThatThrownBy(() -> authService.reissue(null, response))
                .isInstanceOf(BusinessException.class)
                .extracting(e -> ((BusinessException) e).getErrorCode())
                .isEqualTo(AuthErrorCode.INVALID_TOKEN);
    }

    @Test
    @DisplayName("새로 발급한 refresh 토큰에는 fid 가 들어 있고, 회전해도 같은 값이 유지된다")
    void familyIdSurvivesRotation() {
        String familyId = jwtProvider.newRefreshFamilyId();
        String first = jwtProvider.createRefreshToken(90_003L, familyId);
        String second = jwtProvider.createRefreshToken(90_003L, familyId);

        assertThat(jwtProvider.refreshFamilyId(jwtProvider.parseRefreshToken(first))).isEqualTo(familyId);
        assertThat(jwtProvider.refreshFamilyId(jwtProvider.parseRefreshToken(second))).isEqualTo(familyId);
        assertThat(first).isNotEqualTo(second); // 토큰 자체는 매번 다르다(jti 가 바뀐다)
    }
}
