package com.gshared.back.job.dto.request;

import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Positive;

/**
 * 데이터셋 원본 업로드 완료 알림. 본문은 <b>선택</b>이다 — 검증 기준은 등록된 Dataset 메타(sizeBytes·dataHash)이고
 * 오브젝트 키는 Backend 가 정하므로, 프런트가 아무것도 보내지 않아도 Backend 는 실제 파일과 메타를 대조할 수 있다.
 * 값을 보내면 그 값과도 대조한다(전송 중 깨진 파일을 한 번 더 걸러 준다).
 */
public record DatasetUploadCompleteRequest(

        /** 보내면 발급받은 키와 같아야 한다. */
        String objectKey,

        @Positive(message = "sizeBytes는 양수여야 합니다.")
        Long sizeBytes,

        @Pattern(regexp = "^[0-9a-fA-F]{64}$", message = "sha256은 64자리 16진수여야 합니다. (0x 없이)")
        String sha256
) {
    public static final DatasetUploadCompleteRequest EMPTY = new DatasetUploadCompleteRequest(null, null, null);
}
