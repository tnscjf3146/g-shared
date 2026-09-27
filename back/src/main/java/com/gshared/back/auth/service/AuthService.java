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
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;
import java.time.Instant;

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

    public String reissue(String refreshToken, HttpServletResponse response) {
        if (refreshToken == null) {
            throw new BusinessException(AuthErrorCode.INVALID_TOKEN);
        }

        Claims claims = jwtProvider.parseRefreshToken(refreshToken);
        Long userId = Long.valueOf(claims.getSubject());

        String storedRefreshToken = refreshTokenRepository.findByUserId(userId)
                .orElseGet(() -> {
                    refreshTokenRepository.deleteByUserId(userId);
                    throw new BusinessException(AuthErrorCode.INVALID_TOKEN);
                });

        if (!refreshToken.equals(storedRefreshToken)) {
            refreshTokenRepository.deleteByUserId(userId);
            throw new BusinessException(AuthErrorCode.INVALID_TOKEN);
        }

        User user = userRepository.findById(userId)
                .orElseThrow(() -> new BusinessException(UserErrorCode.USER_NOT_FOUND));

        String newAccessToken = jwtProvider.createAccessToken(userId, user.getRole());
        String newRefreshToken = jwtProvider.createRefreshToken(userId);
        refreshTokenRepository.save(userId, newRefreshToken, jwtProperties.refreshExpiration());
        cookieUtil.addRefreshTokenCookie(response, newRefreshToken, jwtProperties.refreshExpiration());

        return newAccessToken;
    }

    public void logout(HttpServletRequest request, HttpServletResponse response, Long userId) {
        cookieUtil.deleteCookie(request, response, "refreshToken");
        refreshTokenRepository.deleteByUserId(userId);

        String accessToken = AuthorizationHeaderUtil.extract(request);
        Claims claims = jwtProvider.parseAccessToken(accessToken);

        Duration ttl = Duration.between(Instant.now(), claims.getExpiration().toInstant());
        if (ttl.isPositive()) {
            tokenBlacklistRepository.save(claims.getId(), ttl);
        }
    }
}
