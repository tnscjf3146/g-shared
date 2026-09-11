package com.gshared.back.job.dto.request;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;

/**
 * 결과물 업로드 URL 발급 요청 (규격 §17).
 * fileName 은 오브젝트 키의 마지막 조각이 되므로 경로 구분자·상위 이동을 허용하지 않는다.
 */
public record ResultUploadRequest(

        @NotBlank(message = "fileName은 필수값입니다.")
        @Pattern(regexp = "^[A-Za-z0-9._-]{1,128}$", message = "fileName은 영문·숫자·. _ - 로만 128자 이내여야 합니다.")
        String fileName,

        @NotBlank(message = "contentType은 필수값입니다.")
        String contentType
) {
}
