package com.gshared.back.global.security;

import com.gshared.back.global.exception.ErrorCode;
import com.gshared.back.global.exception.ErrorResponse;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;
import tools.jackson.databind.ObjectMapper;

import java.io.IOException;

@Component
@RequiredArgsConstructor
public class SecurityResponseWriter {

    private final ObjectMapper objectMapper;

    public void writeError(HttpServletResponse response, ErrorCode errorCode) throws IOException {
        response.setStatus(errorCode.getStatus().value());
        response.setContentType("application/json");
        response.setCharacterEncoding("UTF-8");
        response.getWriter().write(objectMapper.writeValueAsString(ErrorResponse.of(errorCode)));
    }
}
