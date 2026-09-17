package com.gshared.back.job.dto.response;

import java.time.Instant;

/**
 * 입력 어댑터(.tar) 업로드 URL 발급 응답. 데이터셋과 같은 모양이다 —
 * 프런트는 uploadUrl 에 본문을 PUT 하고 끝나면 complete 를 부른다.
 */
public record AdapterUploadResponse(
        String objectKey,
        String uploadUrl,
        Instant expiresAt
) {
}
