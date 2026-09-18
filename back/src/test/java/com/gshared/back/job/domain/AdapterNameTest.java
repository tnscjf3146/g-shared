package com.gshared.back.job.domain;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 어댑터 이름 규칙. 이 이름이 그대로 내려받을 파일 이름이 되므로, 경로 구분자·제어문자가 섞이면 안 된다.
 * 프런트 {@code adapterNameSchema} 와 같은 규칙이어야 두 쪽이 같은 값을 받고 거절한다.
 */
class AdapterNameTest {

    @Test
    @DisplayName("한글·영문·숫자와 - _ . 공백은 받는다")
    void accepts() {
        assertThat(AdapterName.isValid("cs-상담봇")).isTrue();
        assertThat(AdapterName.isValid("qwen3 0.6b lora")).isTrue();
        assertThat(AdapterName.isValid("a")).isTrue();
        assertThat(AdapterName.isValid("v2_final.v3")).isTrue();
        assertThat(AdapterName.isValid("x".repeat(AdapterName.MAX_LENGTH))).isTrue();
    }

    @Test
    @DisplayName("파일 이름으로 위험하거나 모호한 값은 거절한다")
    void rejects() {
        assertThat(AdapterName.isValid("bad/name")).isFalse();      // 경로 구분자
        assertThat(AdapterName.isValid("..")).isFalse();            // 상위 경로
        assertThat(AdapterName.isValid(".hidden")).isFalse();       // 점으로 시작
        assertThat(AdapterName.isValid("-flag")).isFalse();         // 하이픈으로 시작
        assertThat(AdapterName.isValid("tab\there")).isFalse();     // 제어문자
        assertThat(AdapterName.isValid("이름 ")).isFalse();          // 공백으로 끝 — trim 전 값은 거절
        assertThat(AdapterName.isValid("x".repeat(AdapterName.MAX_LENGTH + 1))).isFalse();
        assertThat(AdapterName.isValid(null)).isFalse();
        assertThat(AdapterName.isValid("")).isFalse();
    }

    @Test
    @DisplayName("normalize: 앞뒤 공백을 떼고, 빈 값은 \"안 보냄\"과 같이 다룬다")
    void normalize() {
        assertThat(AdapterName.normalize("  cs-상담봇  ")).isEqualTo("cs-상담봇");
        assertThat(AdapterName.normalize("   ")).isNull();
        assertThat(AdapterName.normalize("")).isNull();
        assertThat(AdapterName.normalize(null)).isNull();
    }

    @Test
    @DisplayName("기본 이름은 모델 slug 에서 만들고, 그 자체가 규칙을 통과한다")
    void defaults() {
        assertThat(AdapterName.defaultFor("qwen3-0.6b")).isEqualTo("qwen3-0.6b-lora");
        assertThat(AdapterName.defaultFor(null)).isEqualTo("adapter-lora");
        assertThat(AdapterName.defaultFor("")).isEqualTo("adapter-lora");
        assertThat(AdapterName.defaultFor(".hidden")).isEqualTo("hidden-lora");   // 앞머리 기호는 떼야 규칙을 통과한다
        assertThat(AdapterName.isValid(AdapterName.defaultFor("Qwen/Qwen2.5-7B"))).isTrue();
        assertThat(AdapterName.isValid(AdapterName.defaultFor("x".repeat(200)))).isTrue();
    }
}
