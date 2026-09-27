package com.gshared.back.global.security.handler;

import com.gshared.back.auth.exception.AuthErrorCode;
import com.gshared.back.global.security.SecurityResponseWriter;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.security.core.AuthenticationException;
import org.springframework.security.web.authentication.AuthenticationFailureHandler;
import org.springframework.stereotype.Component;

import java.io.IOException;

@Slf4j
@Component
@RequiredArgsConstructor
public class LoginFailureHandler implements AuthenticationFailureHandler {

    private final SecurityResponseWriter securityResponseWriter;

    @Override
    public void onAuthenticationFailure(HttpServletRequest request, HttpServletResponse response, AuthenticationException exception) throws IOException {
        log.warn("로그인 실패: {}", exception.getMessage());

        securityResponseWriter.writeError(response, AuthErrorCode.INVALID_CREDENTIALS);
    }
}
