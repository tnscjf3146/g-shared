package com.gshared.back.user.domain;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.ObjectMapper;

import java.time.Instant;

import static org.assertj.core.api.Assertions.assertThat;

class WalletChallengeTest {

    private static final Instant ISSUED_AT = Instant.parse("2026-09-07T10:00:00Z");

    private final ObjectMapper objectMapper = new ObjectMapper();

    private WalletChallenge challenge() {
        return new WalletChallenge(
                42L,
                "0xab5801a7d398351b8be11c439e05c5b3259aec9b",
                "gshared.io wants you to sign in with your Ethereum account:",
                31221L,
                ISSUED_AT,
                ISSUED_AT.plusSeconds(300)
        );
    }

    @Test
    @DisplayName("Redis 에 넣고 꺼내는 JSON 직렬화를 왕복해도 값이 보존된다")
    void serializesAndDeserializes() {
        String json = objectMapper.writeValueAsString(challenge());

        WalletChallenge restored = objectMapper.readValue(json, WalletChallenge.class);

        assertThat(restored).isEqualTo(challenge());
    }

    @Test
    @DisplayName("만료 시각 이후에는 만료된 것으로 본다")
    void expiresAfterExpirationTime() {
        WalletChallenge challenge = challenge();

        assertThat(challenge.isExpired(ISSUED_AT)).isFalse();
        assertThat(challenge.isExpired(ISSUED_AT.plusSeconds(299))).isFalse();
        assertThat(challenge.isExpired(ISSUED_AT.plusSeconds(300))).isTrue();
        assertThat(challenge.isExpired(ISSUED_AT.plusSeconds(301))).isTrue();
    }
}
