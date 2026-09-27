package com.gshared.back.user.domain;

import java.time.Instant;

/**
 * 지갑 연결용 1회성 challenge. Redis 에만 저장되며 DB 엔티티가 아니다.
 *
 * message 는 서버가 만든 서명 원문 그대로다. 검증할 때는 클라이언트가 보낸 문장을
 * 신뢰하지 않고 반드시 여기 저장된 값만 해시한다.
 * userId 는 challenge 를 발급받은 사람과 제출하는 사람이 같은지 확인하는 데 쓴다.
 */
public record WalletChallenge(
        Long userId,
        String walletAddress,
        String message,
        long chainId,
        Instant issuedAt,
        Instant expiresAt
) {

    public boolean isExpired(Instant now) {
        return !now.isBefore(expiresAt);
    }
}
