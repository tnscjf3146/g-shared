package com.gshared.back.global.storage;

import com.gshared.back.global.exception.BusinessException;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.stereotype.Component;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.time.Duration;
import java.time.Instant;
import java.util.HexFormat;
import java.util.Optional;
import java.util.regex.Pattern;

/**
 * Backend 자신이 파일을 보관하는 스토리지. 외부 S3/MinIO 없이 MVP 를 돌리기 위한 구현이다.
 *
 * <p>"서명 URL" 은 {@code {publicBaseUrl}/storage/{objectKey}?method=PUT&expires=..&sig=..} 꼴이고,
 * {@link StorageController} 가 서명·만료를 확인한 뒤 파일을 받거나 내준다. 서명은 HMAC-SHA256 이라
 * 비밀키를 모르면 URL 을 위조하거나 만료를 늘릴 수 없다 — S3 presigned URL 과 같은 성질이다.
 *
 * <p>objectKey 는 {@code jobs/123/result.tar} 처럼 슬래시로 나뉜 상대 경로만 허용한다.
 * {@code ..} 나 절대 경로가 섞이면 root 밖으로 나갈 수 있으므로 형식 단계에서 거른다.
 */
@Slf4j
@Component
@EnableConfigurationProperties(StorageProperties.class)
public class LocalObjectStorage implements ObjectStorage {

    // 영문·숫자·. _ - 로 된 조각들을 / 로 이은 것. 빈 조각·.. 조각은 불허.
    private static final Pattern OBJECT_KEY = Pattern.compile("^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$");
    private static final String HMAC = "HmacSHA256";

    private final StorageProperties properties;
    private final Path root;

    public LocalObjectStorage(StorageProperties properties) {
        this.properties = properties;
        this.root = Path.of(properties.root()).toAbsolutePath().normalize();
    }

    @Override
    public SignedUrl presignUpload(String objectKey, String contentType, Duration ttl) {
        return presign("PUT", objectKey, ttl);
    }

    @Override
    public SignedUrl presignDownload(String objectKey, Duration ttl) {
        return presign("GET", objectKey, ttl);
    }

    private SignedUrl presign(String method, String objectKey, Duration ttl) {
        validateKey(objectKey);
        long expires = Instant.now().plus(ttl).getEpochSecond();
        String signature = sign(method, objectKey, expires);
        String url = stripTrailingSlash(properties.publicBaseUrl())
                + "/storage/" + objectKey
                + "?method=" + method + "&expires=" + expires + "&sig=" + signature;
        return new SignedUrl(url, Instant.ofEpochSecond(expires));
    }

    @Override
    public Optional<ObjectStat> stat(String objectKey) {
        Path file = resolve(objectKey);
        if (!Files.isRegularFile(file)) {
            return Optional.empty();
        }
        try (InputStream in = Files.newInputStream(file)) {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] buffer = new byte[64 * 1024];
            int read;
            while ((read = in.read(buffer)) != -1) {
                digest.update(buffer, 0, read);
            }
            return Optional.of(new ObjectStat(Files.size(file), HexFormat.of().formatHex(digest.digest())));
        } catch (IOException | GeneralSecurityException e) {
            log.error("[Storage] stat 실패 key={}", objectKey, e);
            throw new BusinessException(StorageErrorCode.STORAGE_IO_FAILED);
        }
    }

    // ── StorageController 가 쓰는 입출력 ─────────────────────────

    /** 서명 URL 검증. method·key·expires 가 서명과 맞고 아직 만료 전이어야 한다. */
    public void verify(String method, String objectKey, long expires, String signature) {
        validateKey(objectKey);
        if (Instant.now().getEpochSecond() > expires) {
            throw new BusinessException(StorageErrorCode.INVALID_SIGNATURE);
        }
        String expected = sign(method, objectKey, expires);
        // 길이·내용 비교를 상수 시간으로 — 한 글자씩 맞춰 보는 타이밍 공격 방지
        if (signature == null
                || !MessageDigest.isEqual(expected.getBytes(StandardCharsets.UTF_8),
                                          signature.getBytes(StandardCharsets.UTF_8))) {
            throw new BusinessException(StorageErrorCode.INVALID_SIGNATURE);
        }
    }

    /**
     * 본문을 오브젝트로 저장. 임시 파일에 다 받은 뒤 원자적으로 옮긴다 —
     * 중간에 끊긴 업로드가 "완성된 파일"처럼 보이지 않게.
     */
    public long write(String objectKey, InputStream body) {
        Path file = resolve(objectKey);
        try {
            Files.createDirectories(file.getParent());
            Path temp = Files.createTempFile(file.getParent(), ".upload-", ".part");
            try {
                long size = Files.copy(body, temp, StandardCopyOption.REPLACE_EXISTING);
                Files.move(temp, file, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
                return size;
            } finally {
                Files.deleteIfExists(temp);
            }
        } catch (IOException e) {
            log.error("[Storage] write 실패 key={}", objectKey, e);
            throw new BusinessException(StorageErrorCode.STORAGE_IO_FAILED);
        }
    }

    /** 내려줄 파일 경로. 없으면 404. */
    public Path read(String objectKey) {
        Path file = resolve(objectKey);
        if (!Files.isRegularFile(file)) {
            throw new BusinessException(StorageErrorCode.OBJECT_NOT_FOUND);
        }
        return file;
    }

    // ── 내부 ─────────────────────────────────────────────────────

    private Path resolve(String objectKey) {
        validateKey(objectKey);
        Path file = root.resolve(objectKey).normalize();
        if (!file.startsWith(root)) {
            throw new BusinessException(StorageErrorCode.INVALID_OBJECT_KEY);   // 형식 검사를 통과했어도 한 번 더
        }
        return file;
    }

    private static void validateKey(String objectKey) {
        if (objectKey == null || objectKey.length() > 512 || !OBJECT_KEY.matcher(objectKey).matches()) {
            throw new BusinessException(StorageErrorCode.INVALID_OBJECT_KEY);
        }
    }

    private String sign(String method, String objectKey, long expires) {
        try {
            Mac mac = Mac.getInstance(HMAC);
            mac.init(new SecretKeySpec(properties.signingSecret().getBytes(StandardCharsets.UTF_8), HMAC));
            String payload = method + "\n" + objectKey + "\n" + expires;
            return HexFormat.of().formatHex(mac.doFinal(payload.getBytes(StandardCharsets.UTF_8)));
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("HMAC-SHA256 미지원 JVM", e);
        }
    }

    private static String stripTrailingSlash(String url) {
        return url.endsWith("/") ? url.substring(0, url.length() - 1) : url;
    }
}
