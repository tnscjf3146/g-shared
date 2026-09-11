package com.gshared.back.job.dto.response;

/**
 * 결과물 업로드 완료 응답 (규격 §18). 검증에 실패하면 200 이 아니라 오류 응답이므로 verified 는 항상 true 다 —
 * 규격이 이 필드를 요구해 남겨 둔다.
 */
public record ResultUploadCompleteResponse(
        String jobId,
        String objectKey,
        boolean verified
) {
}
