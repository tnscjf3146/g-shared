package com.gshared.back.global.storage;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.validation.annotation.Validated;

import java.time.Duration;

/**
 * application.yml 의 storage.* 바인딩.
 *
 * <p>TTL 은 "그 시각까지 요청을 <b>시작</b>할 수 있다"는 뜻이다 — 서명·만료 검사는 요청이 들어온 순간 한 번 하고,
 * 이미 열린 스트림은 만료가 지나도 끊지 않는다. 그래서 TTL 은 파일 크기가 아니라
 * "워커가 JOB_ASSIGN 을 받고 다운로드를 시작하기까지 걸릴 수 있는 시간"에 맞추면 된다.
 * 다만 재시도(중단 후 다시 GET)까지 감안해 하한을 둔다 — 너무 짧으면 한 번 끊긴 다운로드를 되살릴 수 없다.
 *
 * @param root          파일을 둘 디렉터리 (컨테이너에서는 볼륨으로 빼야 재시작해도 남는다)
 * @param publicBaseUrl 워커가 접속할 수 있는 Backend 주소. 서명 URL 앞에 붙는다 —
 *                      요청의 Host 헤더로 만들면 프록시 뒤에서 내부 주소가 새어 나가므로 설정값을 쓴다.
 * @param signingSecret URL 서명 HMAC 키. 이 값을 아는 쪽만 유효한 URL 을 만들 수 있다.
 * @param uploadTtl     올리기 URL 유효 시간 (하한 {@link #MIN_TTL})
 * @param downloadTtl   내려받기 URL 유효 시간 (하한 {@link #MIN_TTL})
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

        @NotNull(message = "storage.upload-ttl 설정이 필요합니다.")
        Duration uploadTtl,

        @NotNull(message = "storage.download-ttl 설정이 필요합니다.")
        Duration downloadTtl
) {
    /** 이보다 짧으면 끊긴 다운로드를 재시도할 틈이 없다. 기동 시 거부해 설정 실수를 바로 드러낸다. */
    public static final Duration MIN_TTL = Duration.ofMinutes(10);

    public StorageProperties {
        requireAtLeast("storage.upload-ttl", uploadTtl);
        requireAtLeast("storage.download-ttl", downloadTtl);
    }

    private static void requireAtLeast(String name, Duration ttl) {
        if (ttl != null && ttl.compareTo(MIN_TTL) < 0) {
            throw new IllegalArgumentException(name + " 은 최소 " + MIN_TTL.toMinutes() + "분 이상이어야 합니다: " + ttl);
        }
    }
}
