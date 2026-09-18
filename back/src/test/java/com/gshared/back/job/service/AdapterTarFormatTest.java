package com.gshared.back.job.service;

import com.gshared.back.global.storage.ObjectStorage;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Arrays;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 어댑터 .tar 형식 검사 (lora-adapter-backend-작업 §0).
 *
 * <p>핵심은 두 가지다: ① 완료 Job 의 {@code result.tar}(메트릭 포함)를 그대로 다시 넣을 수 있는가,
 * ② 이름이 접두사 없이 <b>정확히</b> 일치할 때만 받는가. 크기 필드의 512 패딩 계산이 틀리면 두 번째 헤더를
 * 엉뚱한 위치에서 읽게 되므로, 512 의 배수가 아닌 내용도 섞어 둔다.
 */
class AdapterTarFormatTest {

    private static final int BLOCK = 512;
    private static final String KEY = "jobs/1/input/adapter.tar";

    /** 메모리 위의 오브젝트 하나. 검사기는 readRange 만 쓴다. */
    private record InMemoryStorage(byte[] content) implements ObjectStorage {
        @Override
        public SignedUrl presignUpload(String objectKey, String contentType, Duration ttl) {
            throw new UnsupportedOperationException();
        }

        @Override
        public SignedUrl presignDownload(String objectKey, Duration ttl) {
            throw new UnsupportedOperationException();
        }

        @Override
        public SignedUrl presignDownload(String objectKey, Duration ttl, String downloadFilename) {
            throw new UnsupportedOperationException();   // 이 테스트는 다운로드 URL 을 쓰지 않는다
        }

        @Override
        public Optional<ObjectStat> stat(String objectKey) {
            return Optional.of(new ObjectStat(content.length, ""));
        }

        @Override
        public Optional<Long> size(String objectKey) {
            return Optional.of((long) content.length);
        }

        @Override
        public void seal(String objectKey) {
        }

        @Override
        public void delete(String objectKey) {
        }

        @Override
        public Optional<byte[]> readRange(String objectKey, long offset, int length) {
            if (offset >= content.length) {
                return Optional.of(new byte[0]);
            }
            int end = (int) Math.min(content.length, offset + length);
            return Optional.of(Arrays.copyOfRange(content, (int) offset, end));
        }
    }

    /** tar 엔트리 하나: 512바이트 헤더 + 내용(512 배수 패딩). */
    private static byte[] entry(String name, String body, char typeflag) {
        byte[] header = new byte[BLOCK];
        byte[] nameBytes = name.getBytes(StandardCharsets.UTF_8);
        System.arraycopy(nameBytes, 0, header, 0, nameBytes.length);
        byte[] size = String.format("%011o", body.getBytes(StandardCharsets.UTF_8).length).getBytes(StandardCharsets.UTF_8);
        System.arraycopy(size, 0, header, 124, size.length);
        header[156] = (byte) typeflag;

        byte[] content = body.getBytes(StandardCharsets.UTF_8);
        int padded = (content.length + BLOCK - 1) / BLOCK * BLOCK;
        byte[] out = new byte[BLOCK + padded];
        System.arraycopy(header, 0, out, 0, BLOCK);
        System.arraycopy(content, 0, out, BLOCK, content.length);
        return out;
    }

    private static byte[] entry(String name, String body) {
        return entry(name, body, '0');
    }

    /** 엔트리들 + 끝을 알리는 널 블록 2개. */
    private static byte[] tar(byte[]... entries) {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        for (byte[] e : entries) {
            out.writeBytes(e);
        }
        out.writeBytes(new byte[BLOCK * 2]);
        return out.toByteArray();
    }

    private static Optional<String> validate(byte[] content) {
        return AdapterTarFormat.validate(new InMemoryStorage(content), KEY, content.length);
    }

    private static final byte[] CONFIG = entry("adapter_config.json", "{\"r\":8}");
    // 1500바이트 — 512 의 배수가 아니라 패딩 계산이 틀리면 다음 헤더를 못 찾는다
    private static final byte[] WEIGHTS = entry("adapter_model.safetensors", "x".repeat(1500));
    private static final byte[] METRICS = entry("training_metrics.json", "{\"loss\":0.1}");

    @Test
    @DisplayName("필수 2개만 있어도, 완료 Job 의 result.tar(메트릭 포함)여도 통과한다")
    void accepts() {
        assertThat(validate(tar(CONFIG, WEIGHTS))).isEmpty();
        assertThat(validate(tar(CONFIG, WEIGHTS, METRICS))).isEmpty();
        assertThat(validate(tar(WEIGHTS, CONFIG))).isEmpty();   // 순서는 상관없다
    }

    @Test
    @DisplayName("필수 항목이 빠지면 무엇이 없는지 사유에 남는다")
    void rejectsMissingRequired() {
        assertThat(validate(tar(CONFIG))).get().asString().contains("adapter_model.safetensors");
        assertThat(validate(tar(WEIGHTS))).get().asString().contains("adapter_config.json");
        assertThat(validate(tar(METRICS))).get().asString()
                .contains("adapter_config.json").contains("adapter_model.safetensors");
    }

    @Test
    @DisplayName("이름은 접두사 없이 정확히 일치해야 한다 — ./ 도 하위 경로도 거절")
    void rejectsPrefixedNames() {
        assertThat(validate(tar(entry("./adapter_config.json", "{}"), WEIGHTS))).get().asString()
                .contains("허용하지 않는 항목");
        assertThat(validate(tar(entry("sub/adapter_config.json", "{}"), WEIGHTS))).get().asString()
                .contains("허용하지 않는 항목");
        assertThat(validate(tar(CONFIG, WEIGHTS, entry("nope.txt", "x")))).get().asString()
                .contains("nope.txt");
    }

    @Test
    @DisplayName("일반 파일이 아닌 항목(디렉터리·심볼릭 링크)은 거절한다")
    void rejectsNonRegularEntries() {
        assertThat(validate(tar(entry("adapter_config.json", "", '5'), WEIGHTS))).get().asString()
                .contains("일반 파일이 아닌");
        assertThat(validate(tar(entry("adapter_config.json", "", '2'), WEIGHTS))).get().asString()
                .contains("일반 파일이 아닌");
    }

    @Test
    @DisplayName("tar 이 아닌 파일 — 512 배수가 아니거나 헤더가 깨졌으면 거절")
    void rejectsNonTar() {
        assertThat(validate("not a tar".getBytes(StandardCharsets.UTF_8))).get().asString()
                .contains("512의 배수가 아님");
        byte[] broken = tar(CONFIG, WEIGHTS).clone();
        Arrays.fill(broken, 124, 136, (byte) '9');   // 8진수가 아닌 크기 필드
        assertThat(validate(broken)).isNotEmpty();
    }

    @Test
    @DisplayName("빈 tar(널 블록만)은 필수 항목 없음으로 거절")
    void rejectsEmptyArchive() {
        assertThat(validate(tar())).get().asString().contains("필수 항목 없음");
    }
}
