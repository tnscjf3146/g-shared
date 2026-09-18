package com.gshared.back.global.blockchain;

import com.gshared.back.global.wallet.WalletAuthProperties;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.stereotype.Component;

import java.util.Optional;

/**
 * 기동 시 에스크로 설정이 말이 되는지 확인한다. 아니면 <b>뜨지 않는다</b>.
 *
 * <p>거부하는 것:
 * <ul>
 *   <li>주소·RPC 중 하나만 있는 반쪽 설정 -- "확인할 수 있다"가 아니라 설정이 덜 된 것이다.
 *   <li>{@code verification-required=true} 인데 체인 설정이 없음 -- 운영이 체인 없이 뜨면
 *       모든 상태 변경이 UNKNOWN 으로 막히거나, 정책을 끄는 실수를 부른다.
 *   <li>노드의 chainId 가 설정({@code wallet-auth.chain-id})과 다름 -- 엉뚱한 체인에 대고 대조하게 된다.
 *   <li>에스크로 주소에 코드가 없음 -- 잘못된 주소거나 아직 배포 전이다.
 *   <li>에스크로의 {@code paymentToken()} 이 설정한 GSC 주소와 다름 -- 다른 토큰으로 예치가 돈다.
 * </ul>
 *
 * <p>체인이 설정됐는데 노드가 답하지 않으면: 운영({@code verification-required})은 기동 실패,
 * 그 외에는 경고만 남기고 뜬다. 로컬 개발자가 노드를 잠깐 내렸다고 서버가 안 뜨면 곤란하지만,
 * 운영이 확인 못 한 채로 뜨면 첫 요청부터 전부 보류된다 -- 그때 알기보다 지금 아는 편이 낫다.
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class EscrowStartupValidator implements ApplicationRunner {

    private final EscrowProperties properties;
    private final EscrowChainClient chain;
    private final WalletAuthProperties walletAuth;

    @Override
    public void run(ApplicationArguments args) {
        if (properties.isHalfConfigured()) {
            throw new IllegalStateException(
                    "escrow.contract-address 와 escrow.rpc-url 은 둘 다 설정하거나 둘 다 비워야 한다.");
        }
        if (!properties.isConfigured()) {
            if (properties.verificationRequired()) {
                throw new IllegalStateException(
                        "escrow.verification-required=true 인데 에스크로 설정(ESCROW_CONTRACT_ADDRESS·ESCROW_RPC_URL)이 없다.");
            }
            log.warn("[CHAIN] 에스크로가 설정되지 않았다 -- 온체인 확인 없이 동작한다 (로컬 개발 전용)");
            return;
        }

        if (properties.keeperPrivateKeyOrNull() == null) {
            log.warn("[KEEPER] ESCROW_KEEPER_PRIVATE_KEY 미설정 — 워커 환불 실패 시 자동 만료 환불이 실행되지 않습니다.");
        }

        Optional<Long> chainId = chain.chainId();
        if (chainId.isEmpty()) {
            unreachable("eth_chainId");
            return;
        }
        if (!chainId.get().equals(walletAuth.chainId())) {
            throw new IllegalStateException("escrow.rpc-url 의 chainId=" + chainId.get()
                    + " 가 wallet-auth.chain-id=" + walletAuth.chainId() + " 와 다르다.");
        }

        Optional<Boolean> hasCode = chain.contractHasCode();
        if (hasCode.isEmpty()) {
            unreachable("eth_getCode");
            return;
        }
        if (!hasCode.get()) {
            throw new IllegalStateException("escrow.contract-address=" + properties.contractAddressOrNull()
                    + " 에 컨트랙트 코드가 없다.");
        }

        String expectedToken = properties.tokenAddressOrNull();
        if (expectedToken != null) {
            Optional<String> token = chain.paymentToken();
            if (token.isEmpty()) {
                unreachable("paymentToken()");
                return;
            }
            if (!token.get().equalsIgnoreCase(expectedToken)) {
                throw new IllegalStateException("에스크로의 paymentToken()=" + token.get()
                        + " 이 escrow.token-address=" + expectedToken + " 와 다르다.");
            }
        }

        log.info("[CHAIN] 에스크로 확인 완료 chainId={} escrow={} token={} verificationRequired={} keeper={}",
                chainId.get(), properties.contractAddressOrNull(), expectedToken,
                properties.verificationRequired(), properties.keeperPrivateKeyOrNull() != null);
    }

    private void unreachable(String what) {
        String message = "[CHAIN] 기동 검사에서 노드가 답하지 않았다 (" + what + ") rpc=" + properties.rpcUrlOrNull();
        if (properties.verificationRequired()) {
            throw new IllegalStateException(message);
        }
        log.warn("{} -- 확인 없이 뜬다", message);
    }
}
