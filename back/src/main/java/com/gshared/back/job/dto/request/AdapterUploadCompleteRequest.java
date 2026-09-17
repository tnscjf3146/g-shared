package com.gshared.back.job.dto.request;

import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Positive;

/**
 * 입력 어댑터 업로드 완료 알림. 본문은 <b>선택</b>이다 — 오브젝트 키는 Backend 가 정하고 검증은 실제 파일로 하므로
 * 아무것도 보내지 않아도 된다. 값을 보내면 그 값과도 대조한다(전송 중 깨진 파일을 한 번 더 걸러 준다).
 *
 * <p>데이터셋과 달리 대조할 "등록된 메타"가 없다 — 어댑터는 사용자가 다른 데서 받아 온 파일이라
 * Backend 가 아는 것은 올라온 오브젝트뿐이다. 그래서 형식 검사(tar 항목 이름)가 그 자리를 대신한다.
 */
public record AdapterUploadCompleteRequest(

        /** 보내면 발급받은 키와 같아야 한다. */
        String objectKey,

        @Positive(message = "sizeBytes는 양수여야 합니다.")
        Long sizeBytes,

        @Pattern(regexp = "^[0-9a-fA-F]{64}$", message = "sha256은 64자리 16진수여야 합니다. (0x 없이)")
        String sha256
) {
    public static final AdapterUploadCompleteRequest EMPTY = new AdapterUploadCompleteRequest(null, null, null);
}
