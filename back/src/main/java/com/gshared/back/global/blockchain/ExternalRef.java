package com.gshared.back.global.blockchain;

import java.security.SecureRandom;
import java.util.HexFormat;

/**
 * 에스크로 컨트랙트의 {@code externalRef}(bytes32) 발급.
 *
 * Backend DB 의 Job 과 온체인 Job 을 잇는 참조값이다. 컨트랙트는 이 값으로 아무것도 검증하지 않고
 * 이벤트에 실어 되돌려 줄 뿐이므로, 의미 있는 값(jobId 등)을 넣지 않고 추측 불가능한 난수로 만든다 —
 * 순번을 쓰면 남의 Job 의 참조값을 미리 알 수 있다.
 */
public final class ExternalRef {

    private static final SecureRandom RANDOM = new SecureRandom();

    private ExternalRef() {
    }

    /** "0x" + 64자리 hex (bytes32). */
    public static String generate() {
        byte[] bytes = new byte[32];
        RANDOM.nextBytes(bytes);
        return "0x" + HexFormat.of().formatHex(bytes);
    }
}
