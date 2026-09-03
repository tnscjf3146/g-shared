package com.gshared.back.global.util;

import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.HttpHeaders;

public final class AuthorizationHeaderUtil {

    private AuthorizationHeaderUtil() {
    }

    public static String extract(HttpServletRequest request) {
        String authorization = request.getHeader(HttpHeaders.AUTHORIZATION);

        if (authorization != null && authorization.startsWith("Bearer ")) {
            return authorization.substring("Bearer ".length());
        }

        return null;
    }
}
