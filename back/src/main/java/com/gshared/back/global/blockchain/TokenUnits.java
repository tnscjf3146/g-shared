package com.gshared.back.global.blockchain;

import java.math.BigInteger;

/**
 * TOKEN(GSC, 사람 단위) ↔ wei(온체인 단위) 변환의 유일한 통로.
 *
 * GCoin 은 decimals = 18 이라 1 GSC = 10^18 wei 다. 변환을 빠뜨리면 금액이 10^18 배
 * 틀리고, 두 곳에서 각자 변환하면 이중 변환 사고가 난다. 그래서 변환은 이 클래스로만 한다 —
 * 체인 경계(트랜잭션 전송·이벤트 수신) 코드 외에는 부를 일이 없어야 한다.
 *
 * DB·API 는 항상 TOKEN 단위(long), 체인은 항상 wei(BigInteger). wei 를 long 에 담으면
 * 9.22 GSC 상당에서 넘치므로 어느 방향으로도 "wei 를 long 으로" 는 존재하지 않는다.
 */
public final class TokenUnits {

    private static final BigInteger WEI_PER_TOKEN = BigInteger.TEN.pow(18);

    private TokenUnits() {
    }

    /** TOKEN → wei. 온체인으로 금액을 보낼 때(createJob 의 price 등)만 쓴다. */
    public static BigInteger toWei(long token) {
        if (token < 0) {
            throw new IllegalArgumentException("금액은 음수일 수 없다: " + token);
        }
        return BigInteger.valueOf(token).multiply(WEI_PER_TOKEN);
    }

    /**
     * wei → TOKEN. 체인에서 읽은 금액(price 등)을 DB/API 단위로 되돌릴 때만 쓴다.
     * 우리 금액은 전부 TOKEN 정수로만 만들어지므로, 10^18 로 나눠떨어지지 않는 값이
     * 왔다는 것은 다른 경로로 만들어진 작업이라는 뜻이다 — 반올림으로 숨기지 않고 거부한다.
     */
    public static long fromWei(BigInteger wei) {
        if (wei == null || wei.signum() < 0) {
            throw new IllegalArgumentException("wei 금액이 없거나 음수다: " + wei);
        }
        BigInteger[] quotientAndRemainder = wei.divideAndRemainder(WEI_PER_TOKEN);
        if (quotientAndRemainder[1].signum() != 0) {
            throw new IllegalArgumentException("TOKEN 정수 단위가 아닌 wei 금액: " + wei);
        }
        // long 범위를 넘으면 ArithmeticException — 잘라서 저장하는 것보다 실패가 낫다.
        return quotientAndRemainder[0].longValueExact();
    }
}
