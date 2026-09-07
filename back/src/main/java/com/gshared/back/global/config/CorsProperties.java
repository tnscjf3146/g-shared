package com.gshared.back.global.config;

import jakarta.validation.constraints.NotEmpty;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.validation.annotation.Validated;

import java.util.List;

/**
 * 브라우저에서 API 를 호출할 수 있는 출처 목록.
 *
 * 프론트(:3000)와 API(:8080)는 호스트가 같아도 포트가 달라 서로 다른 출처다.
 * 동일 출처는 스킴·호스트·포트가 모두 같아야 하므로 로컬과 운영 모두 CORS 가 적용된다.
 * allowCredentials 를 켜는 이상 와일드카드를 쓸 수 없어 출처를 명시한다.
 */
@Validated
@ConfigurationProperties(prefix = "cors")
public record CorsProperties(

        @NotEmpty(message = "cors.allowed-origins 설정이 필요합니다.")
        List<String> allowedOrigins
) {
}
