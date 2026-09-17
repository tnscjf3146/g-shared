package com.gshared.back.job.dto.response;

/** 입력 어댑터 업로드 완료 응답. verified 는 Backend 가 실제 오브젝트를 확인·검사했다는 뜻이다. */
public record AdapterUploadCompleteResponse(
        Long jobId,
        String objectKey,
        boolean verified
) {
}
