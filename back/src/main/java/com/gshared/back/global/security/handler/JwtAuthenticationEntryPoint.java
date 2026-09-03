package com.gshared.back.global.security.handler;

import com.gshared.back.auth.exception.AuthErrorCode;
import com.gshared.back.global.exception.ErrorCode;
import com.gshared.back.global.security.SecurityResponseWriter;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.security.core.AuthenticationException;
import org.springframework.security.web.AuthenticationEntryPoint;
import org.springframework.stereotype.Component;

import java.io.IOException;

@Component
@RequiredArgsConstructor
public class JwtAuthenticationEntryPoint implements AuthenticationEntryPoint {

    private final SecurityResponseWriter securityResponseWriter;

    @Override
    public void commence(HttpServletRequest request, HttpServletResponse response, AuthenticationException authException) throws IOException, ServletException {
        ErrorCode errorCode = (ErrorCode) request.getAttribute("authError");
        securityResponseWriter.writeError(response, errorCode != null ? errorCode : AuthErrorCode.UNAUTHORIZED);
    }
}
