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

    /**
     * 로그인 한 번(= 기기 하나)을 가리키는 패밀리 식별자 클레임.
     *
     * <p>{@code jti} 는 회전할 때마다 바뀌므로 "같은 로그인에서 나온 토큰들" 을 묶지 못한다.
     * 이 값은 로그인 때 한 번 만들어 회전 내내 그대로 따라다니며, 서버가 기기별로 세션을
     * 보관·폐기하는 기준이 된다(수정요청서 BE-08).
     */
    public static final String REFRESH_FAMILY_CLAIM = "fid";

    /** 로그인 때 만들 새 패밀리 식별자 */
    public String newRefreshFamilyId() {
        return UUID.randomUUID().toString();
    }

    public String createRefreshToken(Long id, String familyId) {
        Date now = new Date();
        Date expireDate = new Date(now.getTime() + jwtProperties.refreshExpiration().toMillis());

        return Jwts.builder()
                .id(UUID.randomUUID().toString())
                .subject(String.valueOf(id))
                .claim("tokenType", "REFRESH")
                .claim(REFRESH_FAMILY_CLAIM, familyId)
                .issuedAt(now)
                .expiration(expireDate)
                .signWith(secretKey)
                .compact();
    }

    /**
     * 토큰이 속한 패밀리. 이 클레임이 없으면 패밀리 도입 이전에 발급된 토큰이라 어느 세션인지 알 수 없다 —
     * 임의로 묶으면 남의 세션을 건드릴 수 있으므로 무효로 본다(브라우저는 한 번 다시 로그인하면 된다).
     */
    public String refreshFamilyId(Claims claims) {
        String familyId = claims.get(REFRESH_FAMILY_CLAIM, String.class);
        if (familyId == null || familyId.isBlank()) {
            throw new BusinessException(AuthErrorCode.INVALID_TOKEN);
        }
        return familyId;
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
