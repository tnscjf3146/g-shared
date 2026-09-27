package com.gshared.back.global.wallet;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.time.Duration;
import java.time.Instant;

import static org.assertj.core.api.Assertions.assertThat;

class WalletLoginMessageFactoryTest {

    private static final String ADDRESS = "0xab5801a7d398351b8be11c439e05c5b3259aec9b";
    private static final String CHALLENGE_ID = "0f3270ab-05e1-4dda-aafd-9d68578c8c32";
    private static final Instant ISSUED_AT = Instant.parse("2026-09-07T10:00:00Z");
    private static final Instant EXPIRES_AT = ISSUED_AT.plus(Duration.ofMinutes(5));

    private final WalletLoginMessageFactory factory = new WalletLoginMessageFactory(
            new WalletAuthProperties("gshared.io", "https://gshared.io", 31221L, Duration.ofMinutes(5)));

    @Test
    @DisplayName("SIWE 형식의 서명 원문을 만든다")
    void createsSiweMessage() {
        String message = factory.create(ADDRESS, CHALLENGE_ID, ISSUED_AT, EXPIRES_AT);

        String[] lines = message.split("\n", -1);

        assertThat(lines).hasSize(12);
        assertThat(lines[0]).isEqualTo("gshared.io wants you to sign in with your Ethereum account:");
        assertThat(lines[1]).isEqualTo(ADDRESS);
        assertThat(lines[2]).isEmpty();
        assertThat(lines[3]).isEqualTo("Link this wallet to your gshared account.");
        assertThat(lines[4]).isEmpty();
        assertThat(lines[5]).isEqualTo("URI: https://gshared.io");
        assertThat(lines[6]).isEqualTo("Version: 1");
        assertThat(lines[7]).isEqualTo("Chain ID: 31221");
        assertThat(lines[8]).matches("Nonce: [0-9a-f]{32}");   // 128비트
        assertThat(lines[9]).isEqualTo("Issued At: 2026-09-07T10:00:00Z");
        assertThat(lines[10]).isEqualTo("Expiration Time: 2026-09-07T10:05:00Z");
        assertThat(lines[11]).isEqualTo("Request ID: " + CHALLENGE_ID);
    }

    @Test
    @DisplayName("호출할 때마다 nonce 가 달라진다")
    void generatesNewNonceEveryTime() {
        String first = factory.create(ADDRESS, CHALLENGE_ID, ISSUED_AT, EXPIRES_AT);
        String second = factory.create(ADDRESS, CHALLENGE_ID, ISSUED_AT, EXPIRES_AT);

        assertThat(nonceOf(first)).isNotEqualTo(nonceOf(second));
        assertThat(first).isNotEqualTo(second);
    }

    @Test
    @DisplayName("도메인과 URI 는 설정값에서만 온다")
    void usesConfiguredDomainAndUri() {
        WalletLoginMessageFactory other = new WalletLoginMessageFactory(
                new WalletAuthProperties("evil.example", "https://evil.example", 1L, Duration.ofMinutes(5)));

        String message = other.create(ADDRESS, CHALLENGE_ID, ISSUED_AT, EXPIRES_AT);

        assertThat(message).startsWith("evil.example wants you to sign in");
        assertThat(message).contains("URI: https://evil.example");
        assertThat(message).contains("Chain ID: 1");
    }

    private String nonceOf(String message) {
        return message.lines()
                .filter(line -> line.startsWith("Nonce: "))
                .findFirst()
                .orElseThrow();
    }
}
