package com.gshared.back.global.ratelimit;

import com.gshared.back.global.exception.CommonErrorCode;
import com.gshared.back.global.exception.ErrorResponse;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import tools.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.time.Duration;

/**
 * IP 기반 요청 제한(Rate Limiting).
 * 같은 IP가 1분에 100회를 초과하면 5분간 차단한다.
 * - count:{ip}  : 1분 단위 요청 카운트 (TTL 60초, 지나면 자동 리셋)
 * - block:{ip}  : 차단 플래그 (TTL 300초, 지나면 자동 해제)
 */
@Component
@RequiredArgsConstructor
public class RateLimitFilter extends OncePerRequestFilter {

    /**
     * 1분 허용 횟수. 운영 기본 100. 로컬·테스트는 모든 요청이 127.0.0.1 하나에서 오므로 application-local.yml 이
     * 사실상 끈다 — 통합 테스트 스위트 하나가 100회를 넘기면 뒤쪽 테스트가 429 로 깨진다.
     */
    @Value("${rate-limit.max-requests-per-minute:100}")
    private int maxRequests;
    private static final Duration WINDOW = Duration.ofMinutes(1);   // 카운트 창
    private static final Duration BLOCK_DURATION = Duration.ofMinutes(5); // 차단 시간

    private final StringRedisTemplate redisTemplate;
    private final ObjectMapper objectMapper;

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain filterChain) throws ServletException, IOException {

        String ip = resolveClientIp(request);
        String countKey = "count:" + ip;
        String blockKey = "block:" + ip;

        // ① 이미 차단된 IP면 즉시 429
        if (Boolean.TRUE.equals(redisTemplate.hasKey(blockKey))) {
            writeTooManyRequests(response);
            return;
        }

        // ② 요청 카운트 +1 (없으면 1부터 시작)
        Long count = redisTemplate.opsForValue().increment(countKey);
        // 첫 요청(카운트가 1)일 때만 TTL 설정 → 이후 1분 뒤 자동 리셋
        if (count != null && count == 1L) {
            redisTemplate.expire(countKey, WINDOW);
        }

        // ③ 한도 초과 → 차단 등록 후 429
        if (count != null && count > maxRequests) {
            redisTemplate.opsForValue().set(blockKey, "1", BLOCK_DURATION);
            writeTooManyRequests(response);
            return;
        }

        // ④ 통과
        filterChain.doFilter(request, response);
    }

    /**
     * 실제 클라이언트 IP 추출.
     * nginx 리버스 프록시 뒤에 있으므로 X-Forwarded-For 헤더를 우선 확인한다.
     * (nginx.conf 에 proxy_set_header X-Forwarded-For 설정 필요)
     */
    private String resolveClientIp(HttpServletRequest request) {
        String forwarded = request.getHeader("X-Forwarded-For");
        if (forwarded != null && !forwarded.isBlank()) {
            // 프록시가 여러 단계면 콤마로 이어짐 → 맨 앞이 최초 클라이언트
            return forwarded.split(",")[0].trim();
        }
        return request.getRemoteAddr();
    }

    private void writeTooManyRequests(HttpServletResponse response) throws IOException {
        response.setStatus(CommonErrorCode.TOO_MANY_REQUESTS.getStatus().value()); // 429
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.setCharacterEncoding("UTF-8");
        ErrorResponse body = ErrorResponse.of(CommonErrorCode.TOO_MANY_REQUESTS);
        response.getWriter().write(objectMapper.writeValueAsString(body));
    }
}
