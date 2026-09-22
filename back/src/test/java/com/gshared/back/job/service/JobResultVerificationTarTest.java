package com.gshared.back.job.service;

import com.gshared.back.job.service.JobResultVerificationService.MalformedArchiveException;
import com.gshared.back.job.service.JobResultVerificationService.RangeReader;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * 결과 tar 에서 training_metrics.json 하나만 안전하게 꺼내는 훑기 검증 (가이드 §6·§7).
 * extract 없이 헤더만 따라가므로, 여기서 확인할 것은 "이름·크기를 속인 archive 에 안 속는가"다.
 */
class JobResultVerificationTarTest {

    private static final String METRICS = "training_metrics.json";

    // ── ustar 한 entry 를 손으로 만든다 — 테스트가 tar 라이브러리에 의존하지 않게 ──
    private static byte[] tarEntry(String name, byte[] data, char typeflag) {
        byte[] header = new byte[512];
        byte[] nameBytes = name.getBytes(StandardCharsets.UTF_8);
        System.arraycopy(nameBytes, 0, header, 0, Math.min(nameBytes.length, 100));
        byte[] size = String.format("%011o", data.length).getBytes(StandardCharsets.US_ASCII);
        System.arraycopy(size, 0, header, 124, size.length);
        header[156] = (byte) typeflag;
        Arrays.fill(header, 148, 156, (byte) ' ');   // checksum 자리 — 파서가 검사하지 않으므로 공백이면 충분
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        out.writeBytes(header);
        out.writeBytes(data);
        int pad = (512 - data.length % 512) % 512;
        out.writeBytes(new byte[pad]);
        return out.toByteArray();
    }

    private static byte[] tar(byte[]... entries) {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        for (byte[] e : entries) {
            out.writeBytes(e);
        }
        out.writeBytes(new byte[1024]);   // 종료 블록 2개
        return out.toByteArray();
    }

    private static RangeReader reader(byte[] archive) {
        return (offset, length) -> {
            if (offset >= archive.length) {
                return Optional.of(new byte[0]);
            }
            int end = (int) Math.min(archive.length, offset + length);
            return Optional.of(Arrays.copyOfRange(archive, (int) offset, end));
        };
    }

    private static final byte[] JSON = "{\"lossHistory\":[{\"step\":1,\"loss\":3.2}]}".getBytes(StandardCharsets.UTF_8);

    @Test
    @DisplayName("다른 파일들 사이에서 training_metrics.json 만 꺼낸다 — extract 없이")
    void findsMetricsAmongOtherEntries() {
        byte[] archive = tar(
                tarEntry("adapter_config.json", "{}".getBytes(StandardCharsets.UTF_8), '0'),
                tarEntry("adapter_model.safetensors", new byte[2048], '0'),
                tarEntry(METRICS, JSON, '0'));

        Optional<byte[]> found = JobResultVerificationService.findTarEntry(reader(archive), METRICS, 1024 * 1024);

        assertThat(found).contains(JSON);
    }

    @Test
    @DisplayName("한 단계 디렉터리 밑(out/…)도 인정한다 — Runtime 산출 구조")
    void findsMetricsOneLevelDeep() {
        byte[] archive = tar(tarEntry("output/" + METRICS, JSON, '0'));
        assertThat(JobResultVerificationService.findTarEntry(reader(archive), METRICS, 1024 * 1024)).contains(JSON);
    }

    @Test
    @DisplayName("entry 가 없으면 empty — 예외가 아니다(legacy 결과)")
    void missingEntryIsEmpty() {
        byte[] archive = tar(tarEntry("adapter_config.json", "{}".getBytes(StandardCharsets.UTF_8), '0'));
        assertThat(JobResultVerificationService.findTarEntry(reader(archive), METRICS, 1024 * 1024)).isEmpty();
    }

    @Test
    @DisplayName("traversal 이름(../ ·절대경로·드라이브 문자)은 내용이 같아도 매칭하지 않는다")
    void traversalNamesNeverMatch() {
        assertThat(JobResultVerificationService.isWantedEntry("../" + METRICS, METRICS)).isFalse();
        assertThat(JobResultVerificationService.isWantedEntry("/" + METRICS, METRICS)).isFalse();
        assertThat(JobResultVerificationService.isWantedEntry("C:\\" + METRICS, METRICS)).isFalse();
        assertThat(JobResultVerificationService.isWantedEntry("a/../" + METRICS, METRICS)).isFalse();
        assertThat(JobResultVerificationService.isWantedEntry("a/b/" + METRICS, METRICS)).isFalse();   // 2단계 초과
        assertThat(JobResultVerificationService.isWantedEntry("./" + METRICS, METRICS)).isTrue();
        assertThat(JobResultVerificationService.isWantedEntry("out/" + METRICS, METRICS)).isTrue();

        byte[] archive = tar(tarEntry("../" + METRICS, JSON, '0'));
        assertThat(JobResultVerificationService.findTarEntry(reader(archive), METRICS, 1024 * 1024)).isEmpty();
    }

    @Test
    @DisplayName("크기 상한을 넘는 entry 는 예외 — 조작된 archive 를 통째로 읽지 않는다")
    void oversizedEntryRejected() {
        byte[] big = new byte[4096];
        byte[] archive = tar(tarEntry(METRICS, big, '0'));
        assertThatThrownBy(() -> JobResultVerificationService.findTarEntry(reader(archive), METRICS, 1024))
                .isInstanceOf(MalformedArchiveException.class)
                .hasMessageContaining("too large");
    }

    @Test
    @DisplayName("본문이 잘린 archive 는 예외 — 부분 데이터를 정상인 척 돌려주지 않는다")
    void truncatedBodyRejected() {
        byte[] entry = tarEntry(METRICS, JSON, '0');
        byte[] truncated = Arrays.copyOfRange(entry, 0, 512 + 5);   // 헤더 + 본문 5바이트만
        assertThatThrownBy(() -> JobResultVerificationService.findTarEntry(reader(truncated), METRICS, 1024 * 1024))
                .isInstanceOf(MalformedArchiveException.class)
                .hasMessageContaining("truncated");
    }

    @Test
    @DisplayName("pax·longname 같은 특수 entry(typeflag ≠ '0')는 건너뛴다")
    void specialEntriesSkipped() {
        byte[] archive = tar(
                tarEntry(METRICS, "fake-via-pax".getBytes(StandardCharsets.UTF_8), 'x'),   // pax 헤더로 위장
                tarEntry(METRICS, JSON, '0'));
        assertThat(JobResultVerificationService.findTarEntry(reader(archive), METRICS, 1024 * 1024)).contains(JSON);
    }
}
