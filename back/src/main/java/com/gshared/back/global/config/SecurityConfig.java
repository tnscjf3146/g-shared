package com.gshared.back.global.config;

import com.gshared.back.auth.repository.TokenBlacklistRepository;
import com.gshared.back.global.security.fillter.JwtAuthenticationFilter;
import com.gshared.back.global.security.fillter.LoginFilter;
import com.gshared.back.global.security.handler.JwtAccessDeniedHandler;
import com.gshared.back.global.security.handler.JwtAuthenticationEntryPoint;
import com.gshared.back.global.security.handler.LoginFailureHandler;
import com.gshared.back.global.security.handler.LoginSuccessHandler;
import com.gshared.back.global.security.jwt.JwtProperties;
import com.gshared.back.global.security.jwt.JwtProvider;
import com.gshared.back.global.security.userdetails.CustomUserDetailsService;
import com.gshared.back.global.ratelimit.RateLimitFilter;
import lombok.RequiredArgsConstructor;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpMethod;
import org.springframework.security.authentication.AuthenticationManager;
import org.springframework.security.authentication.ProviderManager;
import org.springframework.security.authentication.dao.DaoAuthenticationProvider;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;
import org.springframework.web.cors.CorsConfiguration;
import org.springframework.web.cors.CorsConfigurationSource;
import org.springframework.web.cors.UrlBasedCorsConfigurationSource;
import tools.jackson.databind.ObjectMapper;

import java.util.List;

@Configuration
@RequiredArgsConstructor
@EnableConfigurationProperties({JwtProperties.class, CorsProperties.class})
public class SecurityConfig {

    private final ObjectMapper objectMapper;
    private final CorsProperties corsProperties;
    private final JwtProvider jwtProvider;
    private final TokenBlacklistRepository tokenBlacklistRepository;
    private final CustomUserDetailsService customUserDetailsService;
    private final LoginSuccessHandler loginSuccessHandler;
    private final LoginFailureHandler loginFailureHandler;
    private final JwtAuthenticationEntryPoint jwtAuthenticationEntryPoint;
    private final JwtAccessDeniedHandler jwtAccessDeniedHandler;
    private final RateLimitFilter rateLimitFilter;

    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http) {
        http
                .csrf(AbstractHttpConfigurer::disable)
                .cors(cors -> cors.configurationSource(corsConfigurationSource()))
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .formLogin(AbstractHttpConfigurer::disable)
                .httpBasic(AbstractHttpConfigurer::disable)

                .authorizeHttpRequests(auth -> auth
                        .requestMatchers("/swagger-ui/**", "/swagger-ui.html", "/v3/api-docs/**").permitAll()

                        // WebSocket 핸드셰이크는 워커 토큰으로 인증(WorkerHandshakeInterceptor) → JWT 필터 통과
                        .requestMatchers("/ws/**").permitAll()

                        // 사람 없이 워커 프로그램이 부르는 곳. 로그인 JWT 가 아니라 워커 토큰을 들고 오므로
                        // 여기서는 통과시키고 WorkerService 가 그 토큰으로 직접 검증한다.
                        // (/api/workers/register 는 사람이 로그인해서 부르므로 아래 authenticated 에 남는다)
                        // ⚠ 이 목록을 고치면 JwtAuthenticationFilter.WORKER_TOKEN_PATHS 도 같이 고쳐야 한다.
                        .requestMatchers(HttpMethod.POST, "/api/workers/authenticate").permitAll()
                        .requestMatchers(HttpMethod.PUT, "/api/workers/*/operator").permitAll()
                        .requestMatchers(HttpMethod.PUT, "/api/workers/*/benchmark").permitAll()
                        // 워커 전용 Job 창구 (execution-context · result-upload) — 역시 워커 토큰
                        .requestMatchers("/api/worker/**").permitAll()
                        // 서명 URL 로 오브젝트를 올리고 내려받는 곳. 인증은 URL 서명(StorageController)이 한다.
                        .requestMatchers("/storage/**").permitAll()

                        .requestMatchers(HttpMethod.POST, "/api/auth/logout").authenticated()
                        .requestMatchers("/api/auth/**").permitAll()

                        .anyRequest().authenticated()
                )

                .exceptionHandling(exception -> exception
                        .authenticationEntryPoint(jwtAuthenticationEntryPoint)
                        .accessDeniedHandler(jwtAccessDeniedHandler)
                )

                // RateLimit 을 인증 필터보다 앞에 → 인증 로직 전에 IP 차단
                .addFilterBefore(rateLimitFilter, UsernamePasswordAuthenticationFilter.class)
                .addFilterAt(loginFilter(authenticationManager()), UsernamePasswordAuthenticationFilter.class)
                .addFilterBefore(new JwtAuthenticationFilter(jwtProvider, tokenBlacklistRepository), UsernamePasswordAuthenticationFilter.class);

        return http.build();
    }

    private LoginFilter loginFilter(AuthenticationManager authenticationManager) {
        return new LoginFilter(authenticationManager, objectMapper, loginSuccessHandler, loginFailureHandler);
    }

    @Bean
    public AuthenticationManager authenticationManager() {
        DaoAuthenticationProvider provider = new DaoAuthenticationProvider(customUserDetailsService);
        provider.setPasswordEncoder(passwordEncoder());

        return new ProviderManager(provider);
    }
    @Bean
    public PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder();
    }

    @Bean
    public CorsConfigurationSource corsConfigurationSource() {
        CorsConfiguration configuration = new CorsConfiguration();
        configuration.setAllowedOrigins(corsProperties.allowedOrigins());
        configuration.setAllowedMethods(List.of("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"));
        // 실제 보안 경계는 허용 출처다. 허용하지 않은 출처는 어떤 헤더를 붙여도 막히므로
        // 헤더는 요청한 값을 그대로 반사한다. (Spring 이 * 를 실제 요청 헤더로 치환한다)
        configuration.setAllowedHeaders(List.of("*"));
        // refreshToken 쿠키를 주고받으려면 필요하다. 이 값을 켜면 출처에 와일드카드를 쓸 수 없다.
        configuration.setAllowCredentials(true);
        // preflight(OPTIONS) 응답 캐시. RateLimitFilter 가 OPTIONS 도 세므로 캐시로 호출을 줄인다.
        configuration.setMaxAge(3600L);

        UrlBasedCorsConfigurationSource source = new UrlBasedCorsConfigurationSource();
        source.registerCorsConfiguration("/api/**", configuration);
        return source;
    }
}
