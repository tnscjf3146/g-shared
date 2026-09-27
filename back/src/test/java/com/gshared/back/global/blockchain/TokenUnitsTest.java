package com.gshared.back.global.blockchain;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.math.BigInteger;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * 단위 변환 경계 테스트. 스프링 없이 도는 순수 단위 테스트 —
 * 10^18 배(10경 배) 금액 사고를 컴파일되는 규칙으로 못 박는다.
 */
class TokenUnitsTest {

    @Test
    @DisplayName("60 TOKEN 은 60 뒤에 0이 18개 붙은 wei 가 된다")
    void token_to_wei() {
        assertThat(TokenUnits.toWei(60))
                .isEqualTo(new BigInteger("60000000000000000000"));
    }

    @Test
    @DisplayName("왕복 변환은 원래 값으로 돌아온다")
    void round_trip() {
        assertThat(TokenUnits.fromWei(TokenUnits.toWei(12345))).isEqualTo(12345L);
        assertThat(TokenUnits.fromWei(TokenUnits.toWei(0))).isZero();
    }

    @Test
    @DisplayName("음수 금액은 거부한다")
    void negative_rejected() {
        assertThatThrownBy(() -> TokenUnits.toWei(-1))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> TokenUnits.fromWei(BigInteger.valueOf(-1)))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    @DisplayName("TOKEN 정수 단위가 아닌 wei 는 반올림하지 않고 거부한다")
    void non_integer_token_rejected() {
        BigInteger halfToken = BigInteger.TEN.pow(18).divide(BigInteger.TWO);
        assertThatThrownBy(() -> TokenUnits.fromWei(halfToken))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    @DisplayName("long 범위를 넘는 TOKEN 은 잘리지 않고 실패한다")
    void overflow_fails_loudly() {
        BigInteger overLongMax = BigInteger.valueOf(Long.MAX_VALUE)
                .add(BigInteger.ONE)
                .multiply(BigInteger.TEN.pow(18));
        assertThatThrownBy(() -> TokenUnits.fromWei(overLongMax))
                .isInstanceOf(ArithmeticException.class);
    }
}
