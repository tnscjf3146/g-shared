package com.gshared.back.global.storage;

import com.gshared.back.global.exception.BusinessException;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.stereotype.Component;
import org.springframework.web.util.UriUtils;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.io.IOException;
import java.io.InputStream;
import java.nio.ByteBuffer;
import java.nio.channels.SeekableByteChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.time.Duration;
import java.time.Instant;
import java.util.Arrays;
import java.util.HexFormat;
import java.util.Optional;
import java.util.regex.Pattern;

/**
 * Backend 자신이 파일을 보관하는 스토리지. 외부 S3/MinIO 없이 MVP 를 돌리기 위한 구현이다.
 *
 * <p>"서명 URL" 은 {@code {publicBaseUrl}/api/storage/{objectKey}?method=PUT&expires=..&sig=..} 꼴이고,
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

    @Override
    public SignedUrl presignDownload(String objectKey, Duration ttl, String downloadFilename) {
        SignedUrl plain = presign("GET", objectKey, ttl);
        if (downloadFilename == null || downloadFilename.isBlank()) {
            return plain;
        }
        // filename 은 서명에 넣지 않는다 — 저장 이름 표시일 뿐 접근 권한과 무관하고,
        // 서명에 묶으면 이름이 바뀔 때마다(어댑터 이름 변경) 기존 URL 형식과 갈라진다.
        String encoded = UriUtils.encodeQueryParam(downloadFilename, StandardCharsets.UTF_8);
        return new SignedUrl(plain.url() + "&filename=" + encoded, plain.expiresAt());
    }

    private SignedUrl presign(String method, String objectKey, Duration ttl) {
        validateKey(objectKey);
        long expires = Instant.now().plus(ttl).getEpochSecond();
        String signature = sign(method, objectKey, expires);
        String url = stripTrailingSlash(properties.publicBaseUrl())
                + "/api/storage/" + objectKey
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

    @Override
    public Optional<Long> size(String objectKey) {
        Path file = resolve(objectKey);
        if (!Files.isRegularFile(file)) {
            return Optional.empty();
        }
        try {
            return Optional.of(Files.size(file));
        } catch (IOException e) {
            log.error("[Storage] size 실패 key={}", objectKey, e);
            throw new BusinessException(StorageErrorCode.STORAGE_IO_FAILED);
        }
    }

    @Override
    public void seal(String objectKey) {
        Path file = resolve(objectKey);
        try {
            Files.createDirectories(file.getParent());
            if (!Files.exists(sealMarker(file))) {
                Files.createFile(sealMarker(file));
            }
        } catch (IOException e) {
            log.error("[Storage] seal 실패 key={}", objectKey, e);
            throw new BusinessException(StorageErrorCode.STORAGE_IO_FAILED);
        }
    }

    @Override
    public void delete(String objectKey) {
        Path file = resolve(objectKey);
        try {
            Files.deleteIfExists(sealMarker(file));
            Files.deleteIfExists(file);
            // 비어 있는 상위 디렉터리(jobs/{id}/input)는 같이 정리 — Job 마다 빈 폴더가 쌓이지 않게. root 는 남긴다.
            Path dir = file.getParent();
            while (dir != null && !dir.equals(root) && dir.startsWith(root) && isEmptyDir(dir)) {
                Files.delete(dir);
                dir = dir.getParent();
            }
        } catch (IOException e) {
            log.error("[Storage] delete 실패 key={}", objectKey, e);
            throw new BusinessException(StorageErrorCode.STORAGE_IO_FAILED);
        }
    }

    @Override
    public Optional<byte[]> readRange(String objectKey, long offset, int length) {
        Path file = resolve(objectKey);
        if (!Files.isRegularFile(file)) {
            return Optional.empty();
        }
        try (SeekableByteChannel channel = Files.newByteChannel(file, StandardOpenOption.READ)) {
            if (offset >= channel.size()) {
                return Optional.of(new byte[0]);
            }
            channel.position(offset);
            ByteBuffer buffer = ByteBuffer.allocate((int) Math.min(length, channel.size() - offset));
            while (buffer.hasRemaining() && channel.read(buffer) != -1) {
                // 짧은 읽기(short read)를 대비해 버퍼가 찰 때까지 반복한다
            }
            return Optional.of(Arrays.copyOf(buffer.array(), buffer.position()));
        } catch (IOException e) {
            log.error("[Storage] readRange 실패 key={} offset={}", objectKey, offset, e);
            throw new BusinessException(StorageErrorCode.STORAGE_IO_FAILED);
        }
    }

    private static boolean isEmptyDir(Path dir) throws IOException {
        if (!Files.isDirectory(dir)) {
            return false;
        }
        try (var entries = Files.list(dir)) {
            return entries.findAny().isEmpty();
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
        if (Files.exists(sealMarker(file))) {
            // 완료 검증(§18)이 끝나 봉인된 오브젝트 — 살아 있는 서명 URL 로도 덮어쓸 수 없다 (바꿔치기 방어).
            throw new BusinessException(StorageErrorCode.OBJECT_SEALED);
        }
        try {
            Files.createDirectories(file.getParent());
            Path temp = Files.createTempFile(file.getParent(), ".upload-", ".part");
            try {
                long size = copyLimited(body, temp);
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

    // ponytail: 전역 고정 상한 — 용량 정책이 파일 종류별로 갈리면 presign 에 크기를 서명으로 묶는 방식으로 승급
    static final long MAX_UPLOAD_BYTES = 10L * 1024 * 1024 * 1024;   // 10 GiB

    /** 상한을 넘는 스트림은 받다 말고 끊는다 — 유효한 서명 URL 하나로 디스크를 가득 채우는 것을 막는다. */
    private static long copyLimited(InputStream body, Path temp) throws IOException {
        try (var out = Files.newOutputStream(temp)) {
            byte[] buffer = new byte[64 * 1024];
            long total = 0;
            int read;
            while ((read = body.read(buffer)) != -1) {
                total += read;
                if (total > MAX_UPLOAD_BYTES) {
                    throw new BusinessException(StorageErrorCode.OBJECT_TOO_LARGE);
                }
                out.write(buffer, 0, read);
            }
            return total;
        }
    }

    /** 봉인 마커 파일 경로 — 오브젝트 옆에 같은 이름 + 접미사. objectKey 정규식상 이 접미사로 끝나는 키는 만들 수 없다. */
    private static Path sealMarker(Path file) {
        return file.resolveSibling(file.getFileName() + ".sealed!");
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
