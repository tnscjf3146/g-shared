package com.gshared.back.user.repository;

import com.gshared.back.user.domain.WalletChallenge;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.data.redis.core.StringRedisTemplate;

import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 실제 Redis 에 붙여 challenge 저장소의 TTL 과 1회성 소비를 확인한다.
 */
@SpringBootTest
class WalletChallengeRepositoryIntegrationTest {

    private static final String KEY_PREFIX = "wallet-challenge:";
    private static final Duration TTL = Duration.ofMinutes(5);
    private static final String ADDRESS = "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266";

    @Autowired
    private WalletChallengeRepository walletChallengeRepository;
    @Autowired
    private StringRedisTemplate redisTemplate;

    private String challengeId;

    @BeforeEach
    void setUp() {
        challengeId = UUID.randomUUID().toString();
    }

    @AfterEach
    void tearDown() {
        redisTemplate.delete(KEY_PREFIX + challengeId);
    }

    @Test
    @DisplayName("저장하면 TTL 이 걸리고 값이 그대로 복원된다")
    void savesWithTtl() {
        WalletChallenge challenge = challenge();

        walletChallengeRepository.save(challengeId, challenge, TTL);

        Long ttlSeconds = redisTemplate.getExpire(KEY_PREFIX + challengeId, TimeUnit.SECONDS);
        assertThat(ttlSeconds).isNotNull().isPositive().isLessThanOrEqualTo(TTL.toSeconds());

        assertThat(walletChallengeRepository.consume(challengeId)).contains(challenge);
    }

    @Test
    @DisplayName("한 번 소비하면 사라진다")
    void consumesOnlyOnce() {
        walletChallengeRepository.save(challengeId, challenge(), TTL);

        assertThat(walletChallengeRepository.consume(challengeId)).isPresent();
        assertThat(walletChallengeRepository.consume(challengeId)).isEmpty();
        assertThat(redisTemplate.hasKey(KEY_PREFIX + challengeId)).isFalse();
    }

    @Test
    @DisplayName("없는 challenge 를 소비하면 비어 있다")
    void returnsEmptyForUnknownChallenge() {
        assertThat(walletChallengeRepository.consume(UUID.randomUUID().toString())).isEmpty();
    }

    @Test
    @DisplayName("동시에 소비를 시도해도 값을 받는 쪽은 하나뿐이다")
    void allowsOnlyOneConcurrentConsumer() throws Exception {
        int threads = 16;
        walletChallengeRepository.save(challengeId, challenge(), TTL);

        ExecutorService pool = Executors.newFixedThreadPool(threads);
        CountDownLatch ready = new CountDownLatch(threads);
        CountDownLatch start = new CountDownLatch(1);

        try {
            List<Future<Optional<WalletChallenge>>> results = new java.util.ArrayList<>();
            for (int i = 0; i < threads; i++) {
                results.add(pool.submit(() -> {
                    ready.countDown();
                    start.await();
                    return walletChallengeRepository.consume(challengeId);
                }));
            }

            ready.await(5, TimeUnit.SECONDS);
            start.countDown();   // 모든 스레드가 준비된 뒤 동시에 출발

            long winners = 0;
            for (Future<Optional<WalletChallenge>> result : results) {
                if (result.get(10, TimeUnit.SECONDS).isPresent()) {
                    winners++;
                }
            }

            assertThat(winners)
                    .as("GETDEL 이 원자적이지 않으면 두 개 이상이 같은 서명을 통과시킬 수 있다")
                    .isEqualTo(1);
        } finally {
            pool.shutdownNow();
        }
    }

    private WalletChallenge challenge() {
        Instant issuedAt = Instant.parse("2026-09-07T10:00:00Z");
        return new WalletChallenge(42L, ADDRESS, "서명 원문", 31221L, issuedAt, issuedAt.plus(TTL));
    }
}
