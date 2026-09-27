package com.gshared.back.global.wallet;

import com.gshared.back.global.exception.BusinessException;
import com.gshared.back.user.exception.UserErrorCode;
import org.springframework.stereotype.Component;
import org.web3j.crypto.Keys;
import org.web3j.crypto.Sign;
import org.web3j.utils.Numeric;

import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.security.SignatureException;
import java.util.Arrays;
import java.util.Locale;
import java.util.regex.Pattern;

/**
 * MetaMask personal_sign(EIP-191) 서명에서 서명자의 지갑 주소를 복구한다.
 *
 * ECDSA 서명은 원문과 서명값만으로 서명자의 공개키를 역산할 수 있다. 개인키는 필요 없다.
 * 복구한 주소가 기대한 주소와 같다면, 그 주소의 개인키를 가진 사람만 만들 수 있는
 * 서명이므로 소유권이 증명된다.
 *
 * personal_sign 은 원문을 그대로 해시하지 않고 "\x19Ethereum Signed Message:\n" 접두어를
 * 붙인다. 접두어가 없으면 서명 대상이 유효한 트랜잭션 데이터일 수 있어, 사용자가 로그인인 줄
 * 알고 송금에 서명하게 만들 수 있다. 접두어는 그 해시가 트랜잭션이 될 수 없게 만든다.
 * 따라서 검증할 때도 같은 접두어를 붙여야 한다. (signedPrefixedMessageToKey 가 처리한다)
 */
@Component
public class EthereumSignatureVerifier {

    // r(32) + s(32) + v(1) = 65바이트 = 16진수 130자
    private static final Pattern SIGNATURE = Pattern.compile("^0x[0-9a-fA-F]{130}$");

    /**
     * @return 복구된 지갑 주소 (소문자 0x 표기)
     */
    public String recoverAddress(String message, String signature) {
        Sign.SignatureData signatureData = toSignatureData(decode(signature));

        BigInteger publicKey;
        try {
            publicKey = Sign.signedPrefixedMessageToKey(
                    message.getBytes(StandardCharsets.UTF_8), signatureData);
        } catch (SignatureException e) {
            throw new BusinessException(UserErrorCode.INVALID_WALLET_SIGNATURE);
        }

        return "0x" + Keys.getAddress(publicKey).toLowerCase(Locale.ROOT);
    }

    private byte[] decode(String signature) {
        if (signature == null || !SIGNATURE.matcher(signature.trim()).matches()) {
            throw new BusinessException(UserErrorCode.INVALID_WALLET_SIGNATURE);
        }
        return Numeric.hexStringToByteArray(signature.trim());
    }

    private Sign.SignatureData toSignatureData(byte[] raw) {
        byte v = raw[64];

        // MetaMask 는 v 를 27/28 로 보낸다. 0/1 로 보내는 지갑도 있어 맞춰준다.
        if (v == 0 || v == 1) {
            v += 27;
        }
        if (v != 27 && v != 28) {
            throw new BusinessException(UserErrorCode.INVALID_WALLET_SIGNATURE);
        }

        byte[] r = Arrays.copyOfRange(raw, 0, 32);
        byte[] s = Arrays.copyOfRange(raw, 32, 64);
        return new Sign.SignatureData(v, r, s);
    }
}
