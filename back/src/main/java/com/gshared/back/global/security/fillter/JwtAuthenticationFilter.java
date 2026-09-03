package com.gshared.back.global.security.fillter;

import com.gshared.back.auth.exception.AuthErrorCode;
import com.gshared.back.auth.repository.TokenBlacklistRepository;
import com.gshared.back.global.exception.BusinessException;
import com.gshared.back.global.security.jwt.JwtProvider;
import com.gshared.back.global.security.userdetails.CustomUserDetails;
import com.gshared.back.global.util.AuthorizationHeaderUtil;
import com.gshared.back.user.domain.Role;
import io.jsonwebtoken.Claims;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;

@Slf4j
@RequiredArgsConstructor
public class JwtAuthenticationFilter extends OncePerRequestFilter {

    private final JwtProvider jwtProvider;
    private final TokenBlacklistRepository tokenBlacklistRepository;

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain filterChain) throws ServletException, IOException {
        String token = AuthorizationHeaderUtil.extract(request);

        if (token != null) {
            try {
                Claims claims = jwtProvider.parseAccessToken(token);

                if (tokenBlacklistRepository.exists(claims.getId())) {
                    throw new BusinessException(AuthErrorCode.INVALID_TOKEN);
                }

                Long id = Long.valueOf(claims.getSubject());
                Role role = Role.valueOf(claims.get("role", String.class));

                CustomUserDetails principal = CustomUserDetails.of(id, role);
                Authentication auth = new UsernamePasswordAuthenticationToken(
                        principal, null, principal.getAuthorities());
                SecurityContextHolder.getContext().setAuthentication(auth);

            } catch (BusinessException e) {
                request.setAttribute("authError", e.getErrorCode());
            } catch (Exception e) {
                log.error("인증 처리 중 예상치 못한 오류", e);
                request.setAttribute("authError", AuthErrorCode.INVALID_TOKEN);
            }
        }

        filterChain.doFilter(request, response);
    }
}
