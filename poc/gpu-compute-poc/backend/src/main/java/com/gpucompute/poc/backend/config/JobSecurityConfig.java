package com.gpucompute.poc.backend.config;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HashMap;
import java.util.Map;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpHeaders;
import org.springframework.http.server.ServerHttpRequest;
import org.springframework.http.server.ServerHttpResponse;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.web.socket.WebSocketHandler;
import org.springframework.web.socket.server.HandshakeInterceptor;

@Component
public class JobSecurityConfig extends OncePerRequestFilter implements HandshakeInterceptor {
    public static final String AUTHENTICATED_WORKER_ID = "authenticatedWorkerId";

    private final boolean enabled;
    private final String operatorToken;
    private final Map<String, String> workerTokens;

    public JobSecurityConfig(
        @Value("${worker.security.enabled:true}") boolean enabled,
        @Value("${worker.security.operator-token:}") String operatorToken,
        @Value("${worker.security.worker-credentials:}") String workerCredentials
    ) {
        this.enabled = enabled;
        this.operatorToken = operatorToken;
        this.workerTokens = parseCredentials(workerCredentials);
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        return !enabled || !request.getRequestURI().startsWith("/api/jobs");
    }

    @Override
    protected void doFilterInternal(
        HttpServletRequest request,
        HttpServletResponse response,
        FilterChain chain
    ) throws ServletException, IOException {
        if (!matches(bearer(request.getHeader(HttpHeaders.AUTHORIZATION)), operatorToken)) {
            response.sendError(HttpServletResponse.SC_UNAUTHORIZED);
            return;
        }
        chain.doFilter(request, response);
    }

    @Override
    public boolean beforeHandshake(
        ServerHttpRequest request,
        ServerHttpResponse response,
        WebSocketHandler wsHandler,
        Map<String, Object> attributes
    ) {
        if (!enabled) {
            return true;
        }
        String supplied = bearer(request.getHeaders().getFirst(HttpHeaders.AUTHORIZATION));
        for (Map.Entry<String, String> credential : workerTokens.entrySet()) {
            if (matches(supplied, credential.getValue())) {
                attributes.put(AUTHENTICATED_WORKER_ID, credential.getKey());
                return true;
            }
        }
        return false;
    }

    @Override
    public void afterHandshake(
        ServerHttpRequest request,
        ServerHttpResponse response,
        WebSocketHandler wsHandler,
        Exception exception
    ) {}

    private static String bearer(String header) {
        return header != null && header.startsWith("Bearer ")
            ? header.substring(7) : "";
    }

    private static boolean matches(String supplied, String expected) {
        if (supplied.isEmpty() || expected.isEmpty()) {
            return false;
        }
        return MessageDigest.isEqual(
            supplied.getBytes(StandardCharsets.UTF_8),
            expected.getBytes(StandardCharsets.UTF_8)
        );
    }

    private static Map<String, String> parseCredentials(String encoded) {
        Map<String, String> result = new HashMap<>();
        if (encoded == null || encoded.isBlank()) {
            return Map.of();
        }
        for (String entry : encoded.split(",")) {
            String[] pair = entry.split("=", 2);
            if (pair.length != 2 || pair[0].isBlank() || pair[1].isBlank()) {
                throw new IllegalArgumentException("invalid worker credential mapping");
            }
            result.put(pair[0], pair[1]);
        }
        return Map.copyOf(result);
    }
}
