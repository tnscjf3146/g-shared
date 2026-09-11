package com.gshared.back.job.dto.request;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;

/**
 * 예치 확인 요청. escrowJobId 는 의뢰자가 createAndFundJob() 을 보내고 JobFunded 이벤트에서 받은
 * 온체인 jobId(uint256)다. JS 정수 정밀도를 넘을 수 있어 문자열로 받는다.
 *
 * <p>개발용 API 다. JOB_ASSIGN·execution-context 가 escrowJobId 를 필수로 싣기 때문에 여기서도 필수다 —
 * 없이 FUNDED 가 되면 매칭이 그 Job 을 배정하지 않는다.
 * TODO: 최종 구조에서는 FE 값을 정본으로 쓰지 않고 Backend 가 JobFunded 이벤트의 externalRef 를 대조해 확정한다.
 */
public record FundJobRequest(

        @NotBlank(message = "escrowJobId는 필수값입니다.")
        @Pattern(regexp = "^[0-9]{1,78}$", message = "escrowJobId는 0 이상의 정수 문자열이어야 합니다.")
        String escrowJobId
) {
}
