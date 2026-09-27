package com.gshared.back.auth.repository;

import lombok.RequiredArgsConstructor;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.data.redis.core.script.RedisScript;
import org.springframework.stereotype.Repository;

import java.time.Duration;
import java.util.List;

/**
 * Refresh 토큰 보관소.
 *
 * <p><b>로그인 한 번 = 패밀리 하나.</b> 키는 사용자당 하나가 아니라 {@code refresh:{userId}:{familyId}} 다.
 * 예전에는 {@code refresh:{userId}} 한 칸뿐이라, 다른 기기에서 로그인하면 먼저 쓰던 기기의 토큰을 덮어썼고
 * 그 기기가 재발급을 시도하면 불일치로 판정돼 둘 다 로그아웃됐다(수정요청서 BE-08).
 *
 * <p><b>회전은 원자적으로.</b> 예전에는 읽고-비교하고-쓰는 세 단계가 따로 놀아서, 탭 두 개가 같은 토큰으로
 * 동시에 재발급하면 둘 다 성공한 뒤 서버가 마지막 것만 기억했다. 브라우저에 남은 쿠키가 그것과 다르면
 * 다음 재발급이 "재사용"으로 판정돼 세션이 통째로 죽었다. 아래 Lua 는 이 셋을 한 번에 처리한다.
 *
 * <p><b>직전 토큰에 유예를 둔다.</b> 회전 직후 짧은 시간 동안은 옛 토큰을 들고 온 요청도 받아 준다 —
 * 응답이 오기 전에 화면이 넘어가면 브라우저가 새 쿠키를 못 받는데, 그것을 도둑질로 취급하면 안 된다.
 * 유예가 지난 뒤의 재사용은 진짜 이상 신호이므로 그때는 **그 패밀리만** 폐기한다(다른 기기는 살아 있다).
 */
@Repository
@RequiredArgsConstructor
public class RefreshTokenRepository {

    /** 회전 결과 — 호출부가 셋을 구분해 다르게 응답한다 */
    public enum RotateResult {
        /** 제시한 토큰이 현재 값이었다 → 새 토큰으로 교체됨 */
        ROTATED,
        /** 유예 중인 직전 토큰이었다 → 회전하지 않고 현재 토큰을 그대로 돌려준다(경합한 쪽) */
        GRACE,
        /** 현재도 직전도 아니다 → 이 패밀리를 폐기했다 */
        REUSED,
    }

    /** 회전 결과와, GRACE 일 때 돌려줄 현재 토큰 */
    public record Rotation(RotateResult result, String currentToken) {
    }

    private final StringRedisTemplate redisTemplate;

    /*
     * KEYS[1] = 현재 토큰 키, KEYS[2] = 직전 토큰 키
     * ARGV[1] = 제시한 토큰, ARGV[2] = 새 토큰, ARGV[3] = 본 TTL(초), ARGV[4] = 유예 TTL(초)
     *
     * 반환: {결과, 현재 토큰}
     */
    private static final RedisScript<List> ROTATE = new DefaultRedisScript<>("""
            local current = redis.call('GET', KEYS[1])
            if current == ARGV[1] then
              redis.call('SET', KEYS[2], ARGV[1], 'EX', ARGV[4])
              redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
              return {'ROTATED', ARGV[2]}
            end
            if redis.call('GET', KEYS[2]) == ARGV[1] and current then
              return {'GRACE', current}
            end
            redis.call('DEL', KEYS[1])
            redis.call('DEL', KEYS[2])
            return {'REUSED', ''}
            """, List.class);

    /** 로그인 — 이 패밀리의 첫 토큰을 심는다 */
    public void save(Long userId, String familyId, String refreshToken, Duration ttl) {
        redisTemplate.opsForValue().set(key(userId, familyId), refreshToken, ttl);
    }

    /**
     * 제시한 토큰을 새 토큰으로 교체한다. 읽기·비교·쓰기가 한 번에 일어나므로 동시에 들어온 요청 중
     * 하나만 회전에 성공하고, 나머지는 {@link RotateResult#GRACE} 로 현재 토큰을 받아 간다.
     */
    public Rotation rotate(Long userId, String familyId, String presented, String newToken, Duration ttl, Duration grace) {
        List<?> result = redisTemplate.execute(
                ROTATE,
                List.of(key(userId, familyId), prevKey(userId, familyId)),
                presented, newToken, String.valueOf(ttl.toSeconds()), String.valueOf(grace.toSeconds()));

        // execute 가 null 을 주는 것은 스크립트가 아예 안 돌았다는 뜻 — 보수적으로 재사용 취급(세션을 열어 두지 않는다)
        if (result == null || result.size() < 2) {
            return new Rotation(RotateResult.REUSED, null);
        }
        return new Rotation(RotateResult.valueOf(String.valueOf(result.get(0))), String.valueOf(result.get(1)));
    }

    /** 로그아웃 — 이 기기(패밀리)만 끊는다. 다른 기기의 로그인은 그대로 둔다 */
    public void delete(Long userId, String familyId) {
        redisTemplate.delete(List.of(key(userId, familyId), prevKey(userId, familyId)));
    }

    private String key(Long userId, String familyId) {
        return "refresh:" + userId + ":" + familyId;
    }

    private String prevKey(Long userId, String familyId) {
        return key(userId, familyId) + ":prev";
    }
}
