package com.gshared.back.job.dto.response;

import java.time.Instant;

/** 결과물 업로드 URL 발급 응답 (규격 §17). expiresAt 은 ISO-8601 UTC. */
public record ResultUploadResponse(
        String objectKey,
        String uploadUrl,
        Instant expiresAt
) {
}
