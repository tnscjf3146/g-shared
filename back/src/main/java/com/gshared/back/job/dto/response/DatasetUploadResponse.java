package com.gshared.back.job.dto.response;

import java.time.Instant;

/**
 * 데이터셋 원본 업로드 URL 발급 응답. 프런트는 uploadUrl 에 파일 본문을 PUT 으로 그대로 올리고,
 * 끝나면 objectKey 를 complete 에 넘긴다. expiresAt 은 ISO-8601 UTC.
 */
public record DatasetUploadResponse(
        String objectKey,
        String uploadUrl,
        Instant expiresAt
) {
}
