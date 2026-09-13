package com.gshared.back.global.blockchain;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * application.yml 의 escrow.* 바인딩.
 *
 * @param contractAddress 배포된 GPUJobEscrow 주소. Worker 는 execution-context 로 받은 이 주소의
 *                        온체인 Job 을 읽어 Backend 값과 대조한다. 아직 배포 전이면 비어 있을 수 있다.
 */
@ConfigurationProperties(prefix = "escrow")
public record EscrowProperties(String contractAddress, String rpcUrl) {

    /** 비어 있으면 null — 응답에 빈 문자열이 "주소"처럼 실려 나가지 않게. */
    public String contractAddressOrNull() {
        return blankToNull(contractAddress);
    }

    /**
     * 온체인 값을 읽을 노드 주소. 비어 있으면 Backend 는 체인을 확인하지 않는다.
     *
     * <p>주소와 RPC 가 <b>둘 다</b> 있어야 검증이 켜진다. 하나만 있는 상태는
     * "확인할 수 있다"가 아니라 설정이 덜 된 것이고, 그때 검증을 켜면 모든 Job 이
     * 확인 불가로 막힌다.
     */
    public String rpcUrlOrNull() {
        return blankToNull(rpcUrl);
    }

    private static String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value;
    }
}
