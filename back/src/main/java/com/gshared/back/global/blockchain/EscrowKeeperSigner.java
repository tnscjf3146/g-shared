package com.gshared.back.global.blockchain;

import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.web3j.crypto.Credentials;
import org.web3j.crypto.RawTransaction;
import org.web3j.crypto.TransactionEncoder;
import org.web3j.utils.Numeric;

import java.math.BigInteger;
import java.util.Optional;

/**
 * keeper 지갑으로 {@code settleTimeout} 트랜잭션을 서명해 보낸다.
 *
 * <p>Backend 가 체인에 <b>쓰는</b> 유일한 자리다. settleTimeout 은 호출자 제한이 없고 수령자·금액을
 * 전부 컨트랙트가 정하므로, 이 키가 새어도 할 수 있는 일은 "만료된 Job 을 의뢰자에게 환불"뿐이다.
 * 그래도 키는 코드·DB 가 아니라 배포 Secret({@code ESCROW_KEEPER_PRIVATE_KEY})으로만 들어온다.
 *
 * <p>서명은 이미 들어와 있던 {@code org.web3j:crypto} 로 하고(legacy 타입, EIP-155 chainId 포함),
 * 전송은 {@link EscrowChainClient#sendRawTransaction} 이다. SSAFY 체인은 가스비 0 이지만 노드가 말하는
 * gasPrice 를 그대로 쓴다 -- 0 이 아니게 바뀌어도 코드는 그대로 동작한다.
 */
@Slf4j
@Component
public class EscrowKeeperSigner {

    /** settleTimeout 의 실측 가스는 5만 안팎이다. 넉넉히 잡되, 가스비가 0 이라 낭비가 없다. */
    private static final BigInteger GAS_LIMIT = BigInteger.valueOf(200_000);

    private final EscrowChainClient chain;
    private final Credentials credentials;   // null 이면 keeper 가 꺼진 것

    public EscrowKeeperSigner(EscrowProperties properties, EscrowChainClient chain) {
        this.chain = chain;
        String key = properties.keeperPrivateKeyOrNull();
        this.credentials = key == null ? null : Credentials.create(key.startsWith("0x") ? key.substring(2) : key);
    }

    public boolean isEnabled() {
        return credentials != null && chain.isConfigured();
    }

    /** keeper 지갑 주소. 로그·운영 확인용. */
    public String address() {
        return credentials == null ? null : credentials.getAddress();
    }

    /**
     * {@code settleTimeout(escrowJobId)} 를 서명해 보낸다. 보냈으면 txHash.
     *
     * <p>nonce 는 매번 노드의 pending 값을 읽는다 -- 서버가 여럿이거나 재시작 뒤에도 겹치지 않게.
     * 두 서버가 같은 nonce 로 동시에 보내면 한쪽은 노드가 거부하고(빈 답), 다음 순찰에 다시 시도한다.
     */
    public Optional<String> settleTimeout(long chainId, String contractAddress, String escrowJobId) {
        if (!isEnabled()) {
            return Optional.empty();
        }
        Optional<BigInteger> nonce = chain.pendingNonce(credentials.getAddress());
        Optional<BigInteger> gasPrice = chain.gasPrice();
        if (nonce.isEmpty() || gasPrice.isEmpty()) {
            return Optional.empty();
        }
        RawTransaction raw = RawTransaction.createTransaction(
                nonce.get(), gasPrice.get(), GAS_LIMIT, contractAddress, BigInteger.ZERO,
                chain.encodeSettleTimeout(escrowJobId));
        byte[] signed = TransactionEncoder.signMessage(raw, chainId, credentials);
        Optional<String> txHash = chain.sendRawTransaction(Numeric.toHexString(signed));
        txHash.ifPresent(hash -> log.info("[KEEPER] settleTimeout 전송 escrowJobId={} nonce={} tx={}",
                escrowJobId, nonce.get(), hash));
        return txHash;
    }
}
