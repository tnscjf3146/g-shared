package com.gshared.back.global.blockchain;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * application.yml 의 escrow.* 바인딩.
 *
 * @param contractAddress 배포된 GPUJobEscrow 주소. Worker 는 execution-context 로 받은 이 주소의
 *                        온체인 Job 을 읽어 Backend 값과 대조한다. 아직 배포 전이면 비어 있을 수 있다.
 */
@ConfigurationProperties(prefix = "escrow")
public record EscrowProperties(String contractAddress) {

    /** 비어 있으면 null — 응답에 빈 문자열이 "주소"처럼 실려 나가지 않게. */
    public String contractAddressOrNull() {
        return contractAddress == null || contractAddress.isBlank() ? null : contractAddress;
    }
}
