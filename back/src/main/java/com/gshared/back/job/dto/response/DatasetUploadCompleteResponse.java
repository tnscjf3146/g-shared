package com.gshared.back.job.dto.response;

/** 데이터셋 원본 업로드 완료 응답. verified 는 Backend 가 실제 오브젝트를 확인했다는 뜻이다. */
public record DatasetUploadCompleteResponse(
        Long jobId,
        String objectKey,
        boolean verified
) {
}
