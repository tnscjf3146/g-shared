package com.gshared.back.auth.repository;

import lombok.RequiredArgsConstructor;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Repository;

import java.time.Duration;

@Repository
@RequiredArgsConstructor
public class TokenBlacklistRepository {

    private final StringRedisTemplate redisTemplate;

    public void save(String jti, Duration ttl) {
        redisTemplate.opsForValue().set(key(jti), "logout", ttl);
    }

    public boolean exists(String jti) {
        return Boolean.TRUE.equals(redisTemplate.hasKey(key(jti)));
    }

    private String key(String jti) {
        return "blacklist:" + jti;
    }
}
