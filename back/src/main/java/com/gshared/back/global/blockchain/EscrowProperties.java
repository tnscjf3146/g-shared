package com.gshared.back.global.blockchain;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * application.yml 의 escrow.* 바인딩.
 *
 * @param contractAddress      배포된 GPUJobEscrow 주소. Worker 는 execution-context 로 받은 이 주소의
 *                             온체인 Job 을 읽어 Backend 값과 대조한다. 아직 배포 전이면 비어 있을 수 있다.
 * @param rpcUrl               온체인 값을 읽을 노드 주소.
 * @param tokenAddress         결제 토큰(GSC) 주소. 작업 생성 응답에 실려 프런트가 approve 를 보낼 곳이 되고,
 *                             기동 검사에서 컨트랙트의 {@code paymentToken()} 과 같은지 대조한다.
 * @param verificationRequired 체인 확인 없이는 상태를 확정하지 않는가(fail closed). 운영은 반드시 true.
 *                             로컬에서 체인 없이 돌릴 때만 false 로 둔다.
 * @param keeperPrivateKey     만료 Job 을 {@code settleTimeout} 으로 정리하는 keeper 지갑의 개인키(hex).
 *                             비어 있으면 keeper 는 돌지 않는다. 배포 Secret 으로만 주입한다.
 */
@ConfigurationProperties(prefix = "escrow")
public record EscrowProperties(String contractAddress, String rpcUrl, String tokenAddress,
                               boolean verificationRequired, String keeperPrivateKey) {

    /** 비어 있으면 null — 응답에 빈 문자열이 "주소"처럼 실려 나가지 않게. */
    public String contractAddressOrNull() {
        return blankToNull(contractAddress);
    }

    /**
     * 온체인 값을 읽을 노드 주소. 비어 있으면 Backend 는 체인을 확인하지 않는다.
     *
     * <p>주소와 RPC 가 <b>둘 다</b> 있어야 검증이 켜진다. 하나만 있는 상태는
     * "확인할 수 있다"가 아니라 설정이 덜 된 것이고, 기동 검사가 그것을 거부한다
     * ({@link EscrowStartupValidator}).
     */
    public String rpcUrlOrNull() {
        return blankToNull(rpcUrl);
    }

    public String tokenAddressOrNull() {
        return blankToNull(tokenAddress);
    }

    public String keeperPrivateKeyOrNull() {
        return blankToNull(keeperPrivateKey);
    }

    /** 주소·RPC 가 모두 있어 체인에 물어볼 수 있는가. */
    public boolean isConfigured() {
        return rpcUrlOrNull() != null && contractAddressOrNull() != null;
    }

    /** 둘 중 하나만 채워진 반쪽 설정인가. 기동 검사가 거부한다. */
    public boolean isHalfConfigured() {
        return (rpcUrlOrNull() == null) != (contractAddressOrNull() == null);
    }

    private static String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value;
    }
}
