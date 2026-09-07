package com.gshared.back.user.dto.response;

import java.time.Instant;

/**
 * message 는 사용자가 MetaMask 팝업에서 서명할 원문이다.
 * 프론트는 이 문장을 재조립하지 말고 받은 그대로 personal_sign 에 넘겨야 한다.
 */
public record WalletChallengeResponse(String challengeId, String message, Instant expiresAt) {

    public static WalletChallengeResponse of(String challengeId, String message, Instant expiresAt) {
        return new WalletChallengeResponse(challengeId, message, expiresAt);
    }
}
