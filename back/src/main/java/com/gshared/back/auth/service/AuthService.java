package com.gshared.back.auth.service;

import com.gshared.back.auth.dto.request.SignupRequest;
import com.gshared.back.auth.exception.AuthErrorCode;
import com.gshared.back.auth.repository.RefreshTokenRepository;
import com.gshared.back.auth.repository.TokenBlacklistRepository;
import com.gshared.back.global.exception.BusinessException;
import com.gshared.back.global.security.jwt.JwtProperties;
import com.gshared.back.global.security.jwt.JwtProvider;
import com.gshared.back.global.util.AuthorizationHeaderUtil;
import com.gshared.back.global.util.CookieUtil;
import com.gshared.back.user.domain.User;
import com.gshared.back.user.exception.UserErrorCode;
import com.gshared.back.user.repository.UserRepository;
import io.jsonwebtoken.Claims;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;
import java.time.Instant;

@Slf4j
@Service
@RequiredArgsConstructor
@Transactional(readOnly = true)
public class AuthService {

    private final UserRepository userRepository;
    private final PasswordEncoder passwordEncoder;
    private final CookieUtil cookieUtil;
    private final RefreshTokenRepository refreshTokenRepository;
    private final TokenBlacklistRepository tokenBlacklistRepository;
    private final JwtProvider jwtProvider;
    private final JwtProperties jwtProperties;

    @Transactional
    public void signup(SignupRequest request) {
        if (userRepository.existsByEmail(request.email())) {
            throw new BusinessException(UserErrorCode.DUPLICATE_EMAIL);
        }

        User user = User.createUser(request.email(), passwordEncoder.encode(request.password()), request.nickname());
        userRepository.save(user);
    }

    /**
     * Refresh 토큰으로 access 토큰을 다시 발급한다.
     *
     * <p>회전은 {@link RefreshTokenRepository#rotate} 가 원자적으로 처리한다. 예전에는 읽기·비교·쓰기가
     * 따로 놀아서, 탭 두 개가 같은 토큰으로 동시에 들어오면 둘 다 200 을 받은 뒤 서버가 마지막 것만
     * 기억했다. 브라우저에 남은 쿠키가 그것과 다르면 **다음 재발급이 재사용으로 판정돼 세션이 영구히
     * 죽었다** — 다시 로그인하기 전까지 복구되지 않았다(수정요청서 BE-08).
     *
     * <p>경합에서 진 쪽은 폐기 대신 현재 토큰을 그대로 받아 간다. 둘 다 같은 토큰을 들고 끝나므로
     * 어느 쪽 응답이 먼저 쿠키에 적히든 세션이 유지된다.
     */
    public String reissue(String refreshToken, HttpServletResponse response) {
        if (refreshToken == null) {
            throw new BusinessException(AuthErrorCode.INVALID_TOKEN);
        }

        Claims claims = jwtProvider.parseRefreshToken(refreshToken);
        Long userId = Long.valueOf(claims.getSubject());
        String familyId = jwtProvider.refreshFamilyId(claims);

        User user = userRepository.findById(userId)
                .orElseThrow(() -> new BusinessException(UserErrorCode.USER_NOT_FOUND));

        String candidate = jwtProvider.createRefreshToken(userId, familyId);
        RefreshTokenRepository.Rotation rotation = refreshTokenRepository.rotate(
                userId, familyId, refreshToken, candidate,
                jwtProperties.refreshExpiration(), jwtProperties.refreshGrace());

        // 유예 밖의 재사용 — 이 패밀리는 이미 폐기됐다(다른 기기의 로그인은 건드리지 않는다)
        if (rotation.result() == RefreshTokenRepository.RotateResult.REUSED) {
            throw new BusinessException(AuthErrorCode.INVALID_TOKEN);
        }

        // ROTATED 면 방금 심은 새 토큰, GRACE 면 경합에서 이긴 쪽이 심어 둔 현재 토큰
        cookieUtil.addRefreshTokenCookie(response, rotation.currentToken(), jwtProperties.refreshExpiration());

        return jwtProvider.createAccessToken(userId, user.getRole());
    }

    /**
     * 로그아웃 — <b>이 기기만</b> 끊는다.
     *
     * <p>예전에는 사용자당 키가 하나여서 로그아웃이 그 사람의 모든 기기를 끊었다. 이제는 쿠키가 가리키는
     * 패밀리만 지운다 — 노트북에서 나간다고 휴대폰까지 로그아웃되면 안 된다(수정요청서 BE-08).
     *
     * <p>쿠키가 없거나 깨져 있어도 로그아웃은 실패하지 않는다. 서버 세션을 못 지우는 경우에도 access 토큰
     * 블랙리스트 등록과 쿠키 삭제는 반드시 끝나야 한다 — 공용 PC 에서 "나갔다" 가 거짓이 되면 안 된다.
     */
    public void logout(HttpServletRequest request, HttpServletResponse response, Long userId, String refreshToken) {
        cookieUtil.deleteCookie(request, response, "refreshToken");

        if (refreshToken != null) {
            try {
                Claims refreshClaims = jwtProvider.parseRefreshToken(refreshToken);
                refreshTokenRepository.delete(userId, jwtProvider.refreshFamilyId(refreshClaims));
            } catch (BusinessException e) {
                // 만료·위조·패밀리 없음(구버전 토큰) — 지울 세션을 특정할 수 없을 뿐이다. 아래는 그대로 진행한다.
                // warn 인 이유: 정상 흐름이라면 로그인한 사람의 쿠키는 늘 읽혀야 한다. 이 줄이 자주 찍히면
                // 배포 직후 구버전 토큰이 도는 중이거나, 남의 쿠키로 로그아웃을 시도하는 중이다 — 둘 다 보고 싶다
                log.warn("[AUTH] 로그아웃 — refresh 쿠키로 세션을 특정하지 못함 (userId={}): {}", userId, e.getMessage());
            }
        }

        String accessToken = AuthorizationHeaderUtil.extract(request);
        Claims claims = jwtProvider.parseAccessToken(accessToken);

        Duration ttl = Duration.between(Instant.now(), claims.getExpiration().toInstant());
        if (ttl.isPositive()) {
            tokenBlacklistRepository.save(claims.getId(), ttl);
        }
    }
}
