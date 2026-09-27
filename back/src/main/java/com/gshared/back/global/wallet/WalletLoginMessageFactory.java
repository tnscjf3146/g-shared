package com.gshared.back.global.wallet;

import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

import java.security.SecureRandom;
import java.time.Instant;
import java.time.format.DateTimeFormatter;
import java.util.HexFormat;

/**
 * 사용자가 MetaMask 팝업에서 읽고 서명하게 될 원문을 만든다. SIWE(EIP-4361) 형식을 따른다.
 *
 * 문장은 반드시 서버가 만든다. 클라이언트가 보낸 문장을 검증에 쓰면 공격자가
 * 원하는 내용을 서명 대상으로 만들 수 있다. 프론트는 이 문장을 재조립하지 않고 그대로 서명한다.
 *
 * 첫 줄의 도메인은 피싱 방지 장치다. 가짜 사이트가 서명을 유도해도 팝업에는 이 도메인이 뜬다.
 */
@Component
@RequiredArgsConstructor
public class WalletLoginMessageFactory {

    private static final int NONCE_BYTES = 16;   // 128비트
    private static final String STATEMENT = "Link this wallet to your gshared account.";

    private final WalletAuthProperties properties;
    private final SecureRandom secureRandom = new SecureRandom();

    public String create(String walletAddress, String challengeId, Instant issuedAt, Instant expiresAt) {
        return """
                %s wants you to sign in with your Ethereum account:
                %s

                %s

                URI: %s
                Version: 1
                Chain ID: %d
                Nonce: %s
                Issued At: %s
                Expiration Time: %s
                Request ID: %s""".formatted(
                properties.domain(),
                walletAddress,
                STATEMENT,
                properties.uri(),
                properties.chainId(),
                generateNonce(),
                DateTimeFormatter.ISO_INSTANT.format(issuedAt),
                DateTimeFormatter.ISO_INSTANT.format(expiresAt),
                challengeId
        );
    }

    /**
     * 매 요청마다 달라지는 1회용 난수.
     * 문장이 고정이면 한 번 유출된 서명을 계속 재사용할 수 있으므로 예측 불가능해야 한다.
     * java.util.Random 은 시드로 다음 값을 예측할 수 있어 쓰지 않는다.
     */
    private String generateNonce() {
        byte[] nonce = new byte[NONCE_BYTES];
        secureRandom.nextBytes(nonce);
        return HexFormat.of().formatHex(nonce);
    }
}
