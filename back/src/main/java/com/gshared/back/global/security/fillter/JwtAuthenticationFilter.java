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
import org.springframework.util.AntPathMatcher;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.List;

@Slf4j
@RequiredArgsConstructor
public class JwtAuthenticationFilter extends OncePerRequestFilter {

    /**
     * 워커 토큰(JWT 아님)으로 인증하는 경로. 여기서는 JWT 파싱을 아예 시도하지 않는다.
     * 시도하면 워커가 접속할 때마다 파싱 실패가 ERROR 로그로 남아 진짜 오류를 덮는다.
     * ⚠ SecurityConfig 의 permitAll 목록과 같이 유지해야 한다.
     */
    private static final List<String> WORKER_TOKEN_PATHS = List.of(
            "/api/workers/authenticate",
            "/api/workers/*/operator",
            "/api/workers/*/benchmark",
            "/api/worker/**",
            "/storage/**");

    private static final AntPathMatcher PATH_MATCHER = new AntPathMatcher();

    private final JwtProvider jwtProvider;
    private final TokenBlacklistRepository tokenBlacklistRepository;

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        String path = request.getRequestURI();
        return WORKER_TOKEN_PATHS.stream().anyMatch(pattern -> PATH_MATCHER.match(pattern, path));
    }

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
