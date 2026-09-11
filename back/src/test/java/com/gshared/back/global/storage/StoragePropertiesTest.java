package com.gshared.back.global.storage;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.time.Duration;

import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * 서명 URL TTL 하한. 너무 짧은 값은 끊긴 다운로드를 재시도할 틈을 없애므로 기동 시점에 거부한다.
 */
class StoragePropertiesTest {

    private static StorageProperties props(Duration upload, Duration download) {
        return new StorageProperties("./tmp", "http://localhost:8080", "secret", upload, download);
    }

    @Test
    @DisplayName("하한(10분) 이상이면 통과한다")
    void acceptsTtlAtOrAboveMinimum() {
        assertThatCode(() -> props(StorageProperties.MIN_TTL, Duration.ofHours(2))).doesNotThrowAnyException();
    }

    @Test
    @DisplayName("download-ttl 이 하한보다 짧으면 기동을 거부한다")
    void rejectsShortDownloadTtl() {
        assertThatThrownBy(() -> props(Duration.ofHours(1), Duration.ofMinutes(9)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("storage.download-ttl");
    }

    @Test
    @DisplayName("upload-ttl 이 하한보다 짧으면 기동을 거부한다")
    void rejectsShortUploadTtl() {
        assertThatThrownBy(() -> props(Duration.ofSeconds(30), Duration.ofHours(2)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("storage.upload-ttl");
    }
}
