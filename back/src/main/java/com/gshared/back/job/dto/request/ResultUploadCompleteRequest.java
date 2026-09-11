package com.gshared.back.job.dto.request;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Positive;

/**
 * 결과물 업로드 완료 알림 (규격 §18). Backend 는 신고값을 믿지 않고 실제 오브젝트의 크기·해시와 대조한다.
 */
public record ResultUploadCompleteRequest(

        @NotBlank(message = "objectKey는 필수값입니다.")
        String objectKey,

        @NotNull(message = "sizeBytes는 필수값입니다.")
        @Positive(message = "sizeBytes는 양수여야 합니다.")
        Long sizeBytes,

        @NotBlank(message = "sha256은 필수값입니다.")
        @Pattern(regexp = "^[0-9a-fA-F]{64}$", message = "sha256은 64자리 16진수여야 합니다. (0x 없이)")
        String sha256
) {
}
