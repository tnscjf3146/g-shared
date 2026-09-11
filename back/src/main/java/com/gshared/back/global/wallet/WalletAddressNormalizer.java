package com.gshared.back.global.wallet;

import com.gshared.back.global.exception.BusinessException;
import com.gshared.back.global.exception.ErrorCode;
import com.gshared.back.user.exception.UserErrorCode;
import org.springframework.stereotype.Component;

import java.util.Locale;
import java.util.regex.Pattern;

/**
 * 지갑 주소를 저장·비교용 형식으로 통일한다.
 * 이더리움 주소는 16진수라 같은 주소를 대소문자만 다르게 표기할 수 있다.
 * (EIP-55 체크섬 표기 vs 전부 소문자)
 * 정규화하지 않으면 같은 지갑이 서로 다른 문자열로 저장되어 unique 제약을 우회할 수 있다.
 */
@Component
public class WalletAddressNormalizer {

    // "0x" + 16진수 40자 = 42자 고정
    private static final Pattern ADDRESS = Pattern.compile("^0x[0-9a-fA-F]{40}$");

    public String normalize(String walletAddress) {
        return normalize(walletAddress, UserErrorCode.INVALID_WALLET_ADDRESS);
    }

    /**
     * 형식 규칙은 같지만 어느 주소가 틀렸는지(회원 지갑 / 워커 operator)에 따라 응답 코드가 달라야
     * 클라이언트가 고칠 곳을 안다. 그래서 실패 코드를 호출자가 정한다.
     */
    public String normalize(String address, ErrorCode onInvalid) {
        if (address == null) {
            throw new BusinessException(onInvalid);
        }

        String trimmed = address.trim();
        if (!ADDRESS.matcher(trimmed).matches()) {
            throw new BusinessException(onInvalid);
        }

        return trimmed.toLowerCase(Locale.ROOT);
    }
}
