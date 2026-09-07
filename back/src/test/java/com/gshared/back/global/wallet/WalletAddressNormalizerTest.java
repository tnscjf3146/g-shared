package com.gshared.back.global.wallet;

import com.gshared.back.global.exception.BusinessException;
import com.gshared.back.user.exception.UserErrorCode;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.NullSource;
import org.junit.jupiter.params.provider.ValueSource;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class WalletAddressNormalizerTest {

    private static final String CHECKSUM = "0xAb5801a7D398351b8bE11C439e05C5B3259aEc9B";
    private static final String LOWER = "0xab5801a7d398351b8be11c439e05c5b3259aec9b";

    private final WalletAddressNormalizer normalizer = new WalletAddressNormalizer();

    @Test
    @DisplayName("체크섬 표기 주소를 소문자로 정규화한다")
    void normalizesToLowerCase() {
        assertThat(normalizer.normalize(CHECKSUM)).isEqualTo(LOWER);
    }

    @Test
    @DisplayName("대소문자만 다른 같은 주소는 같은 결과가 된다")
    void sameAddressRegardlessOfCase() {
        assertThat(normalizer.normalize(CHECKSUM))
                .isEqualTo(normalizer.normalize(LOWER))
                .isEqualTo(normalizer.normalize(CHECKSUM.toUpperCase().replace("0X", "0x")));
    }

    @Test
    @DisplayName("앞뒤 공백을 제거한다")
    void trimsWhitespace() {
        assertThat(normalizer.normalize("  " + CHECKSUM + "  ")).isEqualTo(LOWER);
    }

    @ParameterizedTest
    @NullSource
    @ValueSource(strings = {
            "",
            "0x",
            "0x123",
            "Ab5801a7D398351b8bE11C439e05C5B3259aEc9B",   // 0x 접두어 없음
            "0xAb5801a7D398351b8bE11C439e05C5B3259aEc9",   // 39자
            "0xAb5801a7D398351b8bE11C439e05C5B3259aEc9BB", // 41자
            "0xZb5801a7D398351b8bE11C439e05C5B3259aEc9B",  // 16진수가 아닌 문자
    })
    @DisplayName("형식이 잘못된 주소는 INVALID_WALLET_ADDRESS 로 거부한다")
    void rejectsMalformedAddress(String malformed) {
        assertThatThrownBy(() -> normalizer.normalize(malformed))
                .isInstanceOf(BusinessException.class)
                .extracting("errorCode")
                .isEqualTo(UserErrorCode.INVALID_WALLET_ADDRESS);
    }
}
