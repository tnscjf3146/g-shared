package com.gshared.back.global.storage;

import jakarta.validation.constraints.NotBlank;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.validation.annotation.Validated;

import java.time.Duration;

/**
 * application.yml 의 storage.* 바인딩.
 *
 * @param root          파일을 둘 디렉터리 (컨테이너에서는 볼륨으로 빼야 재시작해도 남는다)
 * @param publicBaseUrl 워커가 접속할 수 있는 Backend 주소. 서명 URL 앞에 붙는다 —
 *                      요청의 Host 헤더로 만들면 프록시 뒤에서 내부 주소가 새어 나가므로 설정값을 쓴다.
 * @param signingSecret URL 서명 HMAC 키. 이 값을 아는 쪽만 유효한 URL 을 만들 수 있다.
 * @param uploadTtl     올리기 URL 유효 시간
 * @param downloadTtl   내려받기 URL 유효 시간 — 데이터셋 다운로드가 끝날 만큼은 넉넉해야 한다
 */
@Validated
@ConfigurationProperties(prefix = "storage")
public record StorageProperties(

        @NotBlank(message = "storage.root 설정이 필요합니다.")
        String root,

        @NotBlank(message = "storage.public-base-url 설정이 필요합니다.")
        String publicBaseUrl,

        @NotBlank(message = "storage.signing-secret 설정이 필요합니다.")
        String signingSecret,

        Duration uploadTtl,

        Duration downloadTtl
) {
}
