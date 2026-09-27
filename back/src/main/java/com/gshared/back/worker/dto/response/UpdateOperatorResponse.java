package com.gshared.back.worker.dto.response;

/** operator 주소 변경 응답 (규격 §5). 저장된(정규화된) 주소를 되돌려 준다. */
public record UpdateOperatorResponse(
        String workerId,
        String operatorAddress,
        boolean updated
) {
}
