package com.gshared.back.auth.repository;

import com.gshared.back.auth.repository.RefreshTokenRepository.RotateResult;
import com.gshared.back.auth.repository.RefreshTokenRepository.Rotation;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.data.redis.core.StringRedisTemplate;

import java.time.Duration;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 실제 Redis 에 붙여 refresh 토큰 회전을 확인한다 — 수정요청서 BE-08.
 *
 * <p>여기서 지키려는 성질은 하나다: <b>정상적인 사용으로는 세션이 죽지 않는다.</b>
 * 기기를 더 붙여도, 탭 두 개가 동시에 재발급해도, 한 번의 응답을 놓쳐도 다시 로그인하라는 말을 들으면 안 된다.
 * 대신 유예가 지난 뒤의 재사용(진짜 이상 신호)은 그 세션만 끊는다.
 */
@SpringBootTest
class RefreshTokenRepositoryIntegrationTest {

    private static final Long USER_ID = 90_001L;
    private static final Duration TTL = Duration.ofMinutes(10);
    private static final Duration GRACE = Duration.ofSeconds(30);

    @Autowired
    private RefreshTokenRepository repository;
    @Autowired
    private StringRedisTemplate redisTemplate;

    private String family;
    private String familyB;

    @BeforeEach
    void setUp() {
        family = UUID.randomUUID().toString();
        familyB = UUID.randomUUID().toString();
    }

    @AfterEach
    void tearDown() {
        repository.delete(USER_ID, family);
        repository.delete(USER_ID, familyB);
    }

    private static String token(String tag) {
        return "token-" + tag + "-" + UUID.randomUUID();
    }

    @Test
    @DisplayName("현재 토큰으로 회전하면 새 토큰으로 바뀐다")
    void rotatesWithCurrentToken() {
        String first = token("1");
        repository.save(USER_ID, family, first, TTL);

        String second = token("2");
        Rotation rotation = repository.rotate(USER_ID, family, first, second, TTL, GRACE);

        assertThat(rotation.result()).isEqualTo(RotateResult.ROTATED);
        assertThat(rotation.currentToken()).isEqualTo(second);
        // 다음 회전은 새 토큰으로만 된다
        assertThat(repository.rotate(USER_ID, family, second, token("3"), TTL, GRACE).result())
                .isEqualTo(RotateResult.ROTATED);
    }

    @Test
    @DisplayName("응답을 놓쳐 옛 토큰으로 다시 와도, 유예 안이면 현재 토큰을 받아 세션이 이어진다")
    void oldTokenWithinGraceKeepsSessionAlive() {
        String first = token("1");
        repository.save(USER_ID, family, first, TTL);
        String second = token("2");
        repository.rotate(USER_ID, family, first, second, TTL, GRACE);

        // 브라우저가 새 쿠키를 못 받은 상황 — 옛 토큰으로 다시 온다
        Rotation retry = repository.rotate(USER_ID, family, first, token("3"), TTL, GRACE);

        assertThat(retry.result()).isEqualTo(RotateResult.GRACE);
        assertThat(retry.currentToken()).isEqualTo(second); // 새로 돌리지 않고 현재 것을 그대로 준다
        // 받아 간 토큰으로 다음 회전이 된다 = 세션이 살아 있다
        assertThat(repository.rotate(USER_ID, family, retry.currentToken(), token("4"), TTL, GRACE).result())
                .isEqualTo(RotateResult.ROTATED);
    }

    @Test
    @DisplayName("탭 두 개가 같은 토큰으로 동시에 재발급해도 세션이 죽지 않는다")
    void concurrentRotationKeepsExactlyOneWinnerAndNoDeadSession() throws Exception {
        String first = token("1");
        repository.save(USER_ID, family, first, TTL);

        int threads = 8;
        ExecutorService pool = Executors.newFixedThreadPool(threads);
        CountDownLatch start = new CountDownLatch(1);
        try {
            List<Callable<Rotation>> tasks = java.util.stream.IntStream.range(0, threads)
                    .<Callable<Rotation>>mapToObj(i -> () -> {
                        start.await(5, TimeUnit.SECONDS);
                        return repository.rotate(USER_ID, family, first, token("new-" + i), TTL, GRACE);
                    })
                    .toList();

            List<Future<Rotation>> futures = tasks.stream().map(pool::submit).toList();
            start.countDown();

            List<Rotation> results = futures.stream().map(f -> {
                try {
                    return f.get(5, TimeUnit.SECONDS);
                } catch (Exception e) {
                    throw new RuntimeException(e);
                }
            }).toList();

            // 회전에 성공한 것은 정확히 하나, 나머지는 유예로 현재 토큰을 받는다 — 폐기(REUSED)는 없어야 한다
            assertThat(results).filteredOn(r -> r.result() == RotateResult.ROTATED).hasSize(1);
            assertThat(results).noneMatch(r -> r.result() == RotateResult.REUSED);

            // 누가 받았든 모두 같은 토큰을 들고 끝난다 → 어느 응답이 쿠키에 적히든 다음 회전이 된다
            assertThat(results).extracting(Rotation::currentToken).containsOnly(results.get(0).currentToken());
            assertThat(repository.rotate(USER_ID, family, results.get(0).currentToken(), token("after"), TTL, GRACE).result())
                    .isEqualTo(RotateResult.ROTATED);
        } finally {
            pool.shutdownNow();
        }
    }

    @Test
    @DisplayName("유예가 지난 뒤 옛 토큰을 다시 쓰면 그 세션만 폐기된다")
    void reuseAfterGraceRevokesOnlyThatFamily() throws Exception {
        String first = token("1");
        repository.save(USER_ID, family, first, TTL);
        // 다른 기기(다른 패밀리)도 살아 있다
        String otherFirst = token("other-1");
        repository.save(USER_ID, familyB, otherFirst, TTL);

        // 유예를 1초로 두고 회전 → 지나가기를 기다린다
        repository.rotate(USER_ID, family, first, token("2"), TTL, Duration.ofSeconds(1));
        Thread.sleep(1500);

        Rotation reused = repository.rotate(USER_ID, family, first, token("3"), TTL, GRACE);
        assertThat(reused.result()).isEqualTo(RotateResult.REUSED);

        // 폐기된 패밀리는 더 못 쓴다
        assertThat(repository.rotate(USER_ID, family, token("anything"), token("4"), TTL, GRACE).result())
                .isEqualTo(RotateResult.REUSED);
        // 다른 기기는 멀쩡하다 — 예전에는 여기서 같이 죽었다(BE-08)
        assertThat(repository.rotate(USER_ID, familyB, otherFirst, token("other-2"), TTL, GRACE).result())
                .isEqualTo(RotateResult.ROTATED);
    }

    @Test
    @DisplayName("다른 기기에서 로그인해도 먼저 쓰던 기기가 끊기지 않는다")
    void secondDeviceDoesNotEvictTheFirst() {
        String deviceA = token("A1");
        repository.save(USER_ID, family, deviceA, TTL);

        // B 기기 로그인 — 예전에는 같은 키를 덮어써 A 가 죽었다
        repository.save(USER_ID, familyB, token("B1"), TTL);

        assertThat(repository.rotate(USER_ID, family, deviceA, token("A2"), TTL, GRACE).result())
                .isEqualTo(RotateResult.ROTATED);
    }

    @Test
    @DisplayName("로그아웃은 그 기기만 끊는다")
    void deleteRemovesOnlyThatFamily() {
        String deviceA = token("A1");
        String deviceB = token("B1");
        repository.save(USER_ID, family, deviceA, TTL);
        repository.save(USER_ID, familyB, deviceB, TTL);

        repository.delete(USER_ID, family);

        assertThat(repository.rotate(USER_ID, family, deviceA, token("A2"), TTL, GRACE).result())
                .isEqualTo(RotateResult.REUSED);
        assertThat(repository.rotate(USER_ID, familyB, deviceB, token("B2"), TTL, GRACE).result())
                .isEqualTo(RotateResult.ROTATED);
    }

    @Test
    @DisplayName("유예 경계 — 만료 직전이면 받아 주고, 지나면 폐기한다")
    void graceBoundary() throws Exception {
        String first = token("1");
        repository.save(USER_ID, family, first, TTL);
        Duration shortGrace = Duration.ofSeconds(3);
        repository.rotate(USER_ID, family, first, token("2"), TTL, shortGrace);

        // 만료 직전 — 아직 받아 준다
        Thread.sleep(1000);
        assertThat(repository.rotate(USER_ID, family, first, token("x"), TTL, shortGrace).result())
                .isEqualTo(RotateResult.GRACE);

        // 만료 뒤 — 폐기
        Thread.sleep(2500);
        assertThat(repository.rotate(USER_ID, family, first, token("y"), TTL, shortGrace).result())
                .isEqualTo(RotateResult.REUSED);
    }

    @Test
    @DisplayName("유예 안에서 옛 토큰을 되풀이해도 유예가 연장되지는 않는다")
    void graceIsNotExtendedByReplay() throws Exception {
        String first = token("1");
        repository.save(USER_ID, family, first, TTL);
        Duration shortGrace = Duration.ofSeconds(4);
        repository.rotate(USER_ID, family, first, token("2"), TTL, shortGrace);

        // 1초 간격으로 계속 되풀이 — 연장된다면 아래 마지막 호출도 GRACE 여야 한다
        for (int i = 0; i < 3; i++) {
            Thread.sleep(1000);
            repository.rotate(USER_ID, family, first, token("r" + i), TTL, shortGrace);
        }
        Thread.sleep(1500); // 최초 회전으로부터 4.5초 경과

        assertThat(repository.rotate(USER_ID, family, first, token("z"), TTL, shortGrace).result())
                .isEqualTo(RotateResult.REUSED);
    }

    @Test
    @DisplayName("회전한 토큰에는 본 TTL 이, 직전 토큰에는 유예 TTL 이 걸린다")
    void ttlsAreApplied() {
        String first = token("1");
        repository.save(USER_ID, family, first, TTL);
        repository.rotate(USER_ID, family, first, token("2"), TTL, GRACE);

        Long currentTtl = redisTemplate.getExpire("refresh:" + USER_ID + ":" + family, TimeUnit.SECONDS);
        Long prevTtl = redisTemplate.getExpire("refresh:" + USER_ID + ":" + family + ":prev", TimeUnit.SECONDS);

        assertThat(currentTtl).isNotNull().isGreaterThan(TTL.toSeconds() - 30).isLessThanOrEqualTo(TTL.toSeconds());
        assertThat(prevTtl).isNotNull().isGreaterThan(0L).isLessThanOrEqualTo(GRACE.toSeconds());
    }
}
