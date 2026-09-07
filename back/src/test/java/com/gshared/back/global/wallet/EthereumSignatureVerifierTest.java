package com.gshared.back.global.wallet;

import com.gshared.back.global.exception.BusinessException;
import com.gshared.back.user.exception.UserErrorCode;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.NullSource;
import org.junit.jupiter.params.provider.ValueSource;
import org.web3j.crypto.ECKeyPair;
import org.web3j.crypto.Sign;
import org.web3j.utils.Numeric;

import java.nio.charset.StandardCharsets;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class EthereumSignatureVerifierTest {

    // Hardhat 이 공개 문서에 싣는 테스트 전용 계정 #0. 실제 자산이 없는 명백한 더미 키다.
    private static final String PRIVATE_KEY =
            "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80";
    private static final String ADDRESS = "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266";

    // Hardhat 테스트 계정 #1
    private static final String OTHER_PRIVATE_KEY =
            "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d";

    private static final String MESSAGE = """
            gshared.io wants you to sign in with your Ethereum account:
            0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266

            Link this wallet to your gshared account.

            URI: https://gshared.io
            Version: 1
            Chain ID: 31221
            Nonce: 8f2c1e9b4a7d6503f1c8e2b95a4d7e60
            Issued At: 2026-09-07T10:00:00Z
            Expiration Time: 2026-09-07T10:05:00Z
            Request ID: 0f3270ab-05e1-4dda-aafd-9d68578c8c32""";

    private final EthereumSignatureVerifier verifier = new EthereumSignatureVerifier();

    @Test
    @DisplayName("정상 서명에서 서명자의 지갑 주소를 정확히 복구한다")
    void recoversSignerAddress() {
        String signature = sign(MESSAGE, PRIVATE_KEY);

        assertThat(verifier.recoverAddress(MESSAGE, signature)).isEqualTo(ADDRESS);
    }

    @Test
    @DisplayName("문장이 한 글자라도 다르면 다른 주소가 복구된다")
    void differentMessageRecoversDifferentAddress() {
        String signature = sign(MESSAGE, PRIVATE_KEY);
        String tampered = MESSAGE.replace("Chain ID: 31221", "Chain ID: 31222");

        assertThat(verifier.recoverAddress(tampered, signature)).isNotEqualTo(ADDRESS);
    }

    @Test
    @DisplayName("다른 개인키로 서명하면 다른 주소가 복구된다")
    void differentKeyRecoversDifferentAddress() {
        String signature = sign(MESSAGE, OTHER_PRIVATE_KEY);

        assertThat(verifier.recoverAddress(MESSAGE, signature)).isNotEqualTo(ADDRESS);
    }

    @ParameterizedTest
    @NullSource
    @ValueSource(strings = {
            "",
            "0x",
            "0xdeadbeef",                        // 65바이트가 아님
            "not-a-signature",
            "1c000000000000000000000000000000",  // 0x 접두어 없음
    })
    @DisplayName("서명 형식이 잘못되면 INVALID_WALLET_SIGNATURE 로 거부한다")
    void rejectsMalformedSignature(String malformed) {
        assertThatThrownBy(() -> verifier.recoverAddress(MESSAGE, malformed))
                .isInstanceOf(BusinessException.class)
                .extracting("errorCode")
                .isEqualTo(UserErrorCode.INVALID_WALLET_SIGNATURE);
    }

    @Test
    @DisplayName("길이는 맞아도 v 값이 27/28 이 아니면 거부한다")
    void rejectsInvalidRecoveryId() {
        String signature = sign(MESSAGE, PRIVATE_KEY);
        String brokenV = signature.substring(0, signature.length() - 2) + "05";

        assertThatThrownBy(() -> verifier.recoverAddress(MESSAGE, brokenV))
                .isInstanceOf(BusinessException.class)
                .extracting("errorCode")
                .isEqualTo(UserErrorCode.INVALID_WALLET_SIGNATURE);
    }

    @Test
    @DisplayName("지갑이 v 를 0/1 로 보내도 27/28 로 보정해 복구한다")
    void normalizesLegacyRecoveryId() {
        String signature = sign(MESSAGE, PRIVATE_KEY);
        int v = Integer.parseInt(signature.substring(signature.length() - 2), 16);
        String legacyV = signature.substring(0, signature.length() - 2)
                + String.format("%02x", v - 27);

        assertThat(verifier.recoverAddress(MESSAGE, legacyV)).isEqualTo(ADDRESS);
    }

    /** MetaMask 의 personal_sign 과 같은 방식으로 서명해 65바이트 16진수 문자열을 만든다. */
    private String sign(String message, String privateKey) {
        ECKeyPair keyPair = ECKeyPair.create(Numeric.toBigInt(privateKey));
        Sign.SignatureData data =
                Sign.signPrefixedMessage(message.getBytes(StandardCharsets.UTF_8), keyPair);

        byte[] raw = new byte[65];
        System.arraycopy(data.getR(), 0, raw, 0, 32);
        System.arraycopy(data.getS(), 0, raw, 32, 32);
        raw[64] = data.getV()[0];
        return Numeric.toHexString(raw);
    }
}
