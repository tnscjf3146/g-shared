package com.gshared.back.global.security.jwt;

import com.gshared.back.auth.exception.AuthErrorCode;
import com.gshared.back.global.exception.BusinessException;
import com.gshared.back.user.domain.Role;
import io.jsonwebtoken.Claims;
import io.jsonwebtoken.ExpiredJwtException;
import io.jsonwebtoken.JwtException;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import org.springframework.stereotype.Component;

import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;
import java.util.Date;
import java.util.UUID;

@Component
public class JwtProvider {

    private final SecretKey secretKey;
    private final JwtProperties jwtProperties;

    public JwtProvider(JwtProperties jwtProperties) {
        this.secretKey = Keys.hmacShaKeyFor(jwtProperties.secret().getBytes(StandardCharsets.UTF_8));
        this.jwtProperties = jwtProperties;
    }

    public String createAccessToken(Long id, Role role) {
        Date now = new Date();
        Date expireDate = new Date(now.getTime() + jwtProperties.accessExpiration().toMillis());

        return Jwts.builder()
                .id(UUID.randomUUID().toString())
                .subject(String.valueOf(id))
                .claim("role", role.name())
                .claim("tokenType", "ACCESS")
                .issuedAt(now)
                .expiration(expireDate)
                .signWith(secretKey)
                .compact();
    }

    public String createRefreshToken(Long id) {
        Date now = new Date();
        Date expireDate = new Date(now.getTime() + jwtProperties.refreshExpiration().toMillis());

        return Jwts.builder()
                .id(UUID.randomUUID().toString())
                .subject(String.valueOf(id))
                .claim("tokenType", "REFRESH")
                .issuedAt(now)
                .expiration(expireDate)
                .signWith(secretKey)
                .compact();
    }

    public Claims getClaims(String token) {
        try {
            return Jwts.parser()
                    .verifyWith(secretKey)
                    .build()
                    .parseSignedClaims(token)
                    .getPayload();
        } catch (ExpiredJwtException e) {
            throw new BusinessException(AuthErrorCode.EXPIRED_TOKEN);
        } catch (JwtException | IllegalArgumentException e) {
            throw new BusinessException(AuthErrorCode.INVALID_TOKEN);
        }
    }

    public Claims parseAccessToken(String token) {
        return parseWithType(token, "ACCESS");
    }

    public Claims parseRefreshToken(String token) {
        return parseWithType(token, "REFRESH");
    }

    private Claims parseWithType(String token, String expectedType) {
        Claims claims = getClaims(token);

        if (!expectedType.equals(claims.get("tokenType", String.class))) {
            throw new BusinessException(AuthErrorCode.INVALID_TOKEN);
        }

        return claims;
    }
}
