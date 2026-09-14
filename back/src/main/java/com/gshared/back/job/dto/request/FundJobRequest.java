package com.gshared.back.job.dto.request;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;

/**
 * 예치 확인 요청. 의뢰자가 MetaMask 로 createAndFundJob() 을 보내고 받은 트랜잭션 해시다.
 *
 * <p>Backend 는 이 해시의 영수증을 직접 읽어 JobFunded 이벤트를 찾고, externalRef·client·provider·price 를
 * 자기 기록과 대조한 뒤에야 FUNDED 로 바꾼다. 온체인 jobId 는 프런트가 아니라 이벤트에서 읽는다.
 *
 * <p>{@code escrowJobId} 는 <b>체인이 설정되지 않은 로컬 개발</b>에서만 쓰인다 — 대조할 체인이 없으니 받은 값을
 * 그대로 기록한다. 체인이 설정돼 있으면 무시한다.
 */
public record FundJobRequest(

        @NotBlank(message = "txHash는 필수값입니다.")
        @Pattern(regexp = "^0x[0-9a-fA-F]{64}$", message = "txHash는 0x로 시작하는 64자리 16진수여야 합니다.")
        String txHash,

        @Pattern(regexp = "^[0-9]{1,78}$", message = "escrowJobId는 0 이상의 정수 문자열이어야 합니다.")
        String escrowJobId
) {
}
