package com.gshared.back.global.util;

import jakarta.servlet.http.Cookie;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.time.Duration;

@Component
public class CookieUtil {

    private final boolean secure;

    public CookieUtil(@Value("${cookie.secure}") boolean secure) {
        this.secure = secure;
    }

    public void addCookie(HttpServletResponse response, String name, String value, int maxAge) {
        Cookie cookie = new Cookie(name, value);
        cookie.setPath("/");
        cookie.setHttpOnly(true);
        cookie.setSecure(secure);
        cookie.setMaxAge(maxAge);
        response.addCookie(cookie);
    }

    public void deleteCookie(HttpServletRequest request, HttpServletResponse response, String name) {
        Cookie[] cookies = request.getCookies();

        if (cookies == null) {
            return;
        }

        for (Cookie cookie : cookies) {
            if (name.equals(cookie.getName())) {
                cookie.setValue("");
                cookie.setPath("/");
                cookie.setHttpOnly(true);
                cookie.setSecure(secure);
                cookie.setMaxAge(0);
                response.addCookie(cookie);
            }
        }
    }

    public void addRefreshTokenCookie(HttpServletResponse response, String refreshToken, Duration ttl) {
        addCookie(response, "refreshToken", refreshToken, (int) ttl.toSeconds());
    }
}
