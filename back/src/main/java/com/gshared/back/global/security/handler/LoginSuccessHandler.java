package com.gshared.back.global.security.handler;

import com.gshared.back.auth.dto.response.TokenResponse;
import com.gshared.back.auth.repository.RefreshTokenRepository;
import com.gshared.back.global.security.jwt.JwtProperties;
import com.gshared.back.global.security.jwt.JwtProvider;
import com.gshared.back.global.security.userdetails.CustomUserDetails;
import com.gshared.back.global.util.CookieUtil;
import com.gshared.back.user.domain.Role;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.security.core.Authentication;
import org.springframework.security.web.authentication.AuthenticationSuccessHandler;
import org.springframework.stereotype.Component;
import tools.jackson.databind.ObjectMapper;

import java.io.IOException;

@Component
@RequiredArgsConstructor
public class LoginSuccessHandler implements AuthenticationSuccessHandler {

    private final JwtProvider jwtProvider;
    private final JwtProperties jwtProperties;
    private final CookieUtil cookieUtil;
    private final ObjectMapper objectMapper;
    private final RefreshTokenRepository refreshTokenRepository;

    @Override
    public void onAuthenticationSuccess(HttpServletRequest request, HttpServletResponse response, Authentication authentication) throws IOException, ServletException {
        CustomUserDetails userDetails = (CustomUserDetails) authentication.getPrincipal();

        Long id = userDetails.getId();
        Role role = userDetails.getRole();

        String refreshToken = jwtProvider.createRefreshToken(id);
        refreshTokenRepository.save(id, refreshToken, jwtProperties.refreshExpiration());
        addRefreshTokenToCookie(response, refreshToken);

        String accessToken = jwtProvider.createAccessToken(id, role);
        addAccessTokenToBody(response, accessToken);
    }

    private void addAccessTokenToBody(HttpServletResponse response, String accessToken) throws IOException {
        response.setContentType("application/json");
        response.setCharacterEncoding("UTF-8");
        response.getWriter().write(objectMapper.writeValueAsString(TokenResponse.of(accessToken)));
    }

    private void addRefreshTokenToCookie(HttpServletResponse response, String refreshToken) {
        cookieUtil.addRefreshTokenCookie(response, refreshToken, jwtProperties.refreshExpiration());
    }
}
