package com.gshared.back.global.storage;

import jakarta.servlet.http.HttpServletRequest;
import lombok.RequiredArgsConstructor;
import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.util.UriUtils;

import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;

/**
 * 서명 URL 로 오브젝트를 올리고 내려받는 창구. {@link LocalObjectStorage} 의 짝이다.
 *
 * <p>여기는 로그인 JWT 도 워커 토큰도 보지 않는다 — URL 에 담긴 서명이 곧 인증이다.
 * 서명은 method·objectKey·만료 시각에 묶여 있어 GET 용 URL 로 PUT 을 하거나 다른 키를 건드릴 수 없다.
 *
 * <p>objectKey 에 슬래시가 들어가므로 경로 변수 대신 요청 URI 에서 직접 잘라낸다.
 */
@RestController
@RequiredArgsConstructor
public class StorageController {

    private static final String PREFIX = "/storage/";

    private final LocalObjectStorage storage;

    @PutMapping("/storage/**")
    public ResponseEntity<Void> upload(
            @RequestParam("expires") long expires,
            @RequestParam("sig") String signature,
            HttpServletRequest request
    ) throws IOException {
        String objectKey = objectKey(request);
        storage.verify("PUT", objectKey, expires, signature);
        try (InputStream body = request.getInputStream()) {
            storage.write(objectKey, body);
        }
        return ResponseEntity.ok().build();
    }

    @GetMapping("/storage/**")
    public ResponseEntity<Resource> download(
            @RequestParam("expires") long expires,
            @RequestParam("sig") String signature,
            HttpServletRequest request
    ) {
        String objectKey = objectKey(request);
        storage.verify("GET", objectKey, expires, signature);
        Path file = storage.read(objectKey);
        return ResponseEntity.ok()
                .contentType(MediaType.APPLICATION_OCTET_STREAM)
                .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=\"" + file.getFileName() + "\"")
                .body(new FileSystemResource(file));
    }

    /**
     * 요청 URI 에서 objectKey 를 잘라낸다. getRequestURI() 는 인코딩된 원문이라 퍼센트 인코딩을 풀어 준다 —
     * 클라이언트가 {@code jobs%2F1%2Fresult.tar} 로 보내도 서명한 키 {@code jobs/1/result.tar} 와 같은 값이 되게.
     * 풀린 결과에 {@code ..} 같은 것이 섞이면 이후 키 형식 검사가 거른다.
     * ({@link java.net.URLDecoder} 는 {@code +} 를 공백으로 바꿔 경로에 맞지 않으므로 쓰지 않는다)
     */
    private static String objectKey(HttpServletRequest request) {
        String path = UriUtils.decode(request.getRequestURI(), StandardCharsets.UTF_8);
        return path.startsWith(PREFIX) ? path.substring(PREFIX.length()) : "";
    }
}
