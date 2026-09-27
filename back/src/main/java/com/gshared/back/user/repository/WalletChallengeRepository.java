package com.gshared.back.user.repository;

import com.gshared.back.user.domain.WalletChallenge;
import lombok.RequiredArgsConstructor;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Repository;
import tools.jackson.databind.ObjectMapper;

import java.time.Duration;
import java.util.Optional;

@Repository
@RequiredArgsConstructor
public class WalletChallengeRepository {

    private final StringRedisTemplate redisTemplate;
    private final ObjectMapper objectMapper;

    public void save(String challengeId, WalletChallenge challenge, Duration ttl) {
        redisTemplate.opsForValue().set(key(challengeId), objectMapper.writeValueAsString(challenge), ttl);
    }

    /**
     * challenge 를 꺼내면서 동시에 삭제한다(GETDEL).
     *
     * 조회와 삭제를 두 번에 나누면 그 사이에 들어온 요청이 같은 값을 함께 받아
     * 하나의 서명으로 두 번 통과할 수 있다. 값을 받는 요청은 항상 하나뿐이어야 한다.
     * 서명 검증에 실패해도 challenge 는 이미 소비되며, 사용자는 새로 발급받아야 한다.
     */
    public Optional<WalletChallenge> consume(String challengeId) {
        String json = redisTemplate.opsForValue().getAndDelete(key(challengeId));
        if (json == null) {
            return Optional.empty();
        }
        return Optional.of(objectMapper.readValue(json, WalletChallenge.class));
    }

    private String key(String challengeId) {
        return "wallet-challenge:" + challengeId;
    }
}
