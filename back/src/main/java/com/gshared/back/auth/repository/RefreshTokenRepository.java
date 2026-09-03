package com.gshared.back.auth.repository;

import lombok.RequiredArgsConstructor;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Repository;

import java.time.Duration;
import java.util.Optional;

@Repository
@RequiredArgsConstructor
public class RefreshTokenRepository {

    private final StringRedisTemplate redisTemplate;

    public void save(Long userId, String refreshToken, Duration ttl) {
        redisTemplate.opsForValue().set(key(userId), refreshToken, ttl);
    }

    public Optional<String> findByUserId(Long userId) {
        String refreshToken = redisTemplate.opsForValue().get(key(userId));
        return Optional.ofNullable(refreshToken);
    }

    public void deleteByUserId(Long userId) {
        redisTemplate.delete(key(userId));
    }

    private String key(Long userId) {
        return "refresh:" + userId;
    }
}
