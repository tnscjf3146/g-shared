package com.gshared.back.job.service;

import com.gshared.back.job.dto.response.GpuOptionResponse.Fit;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 적합도 판정은 Scheduler 의 feasibility 정책과 같은 기준이어야 한다.
 *
 * <p>Scheduler 가 1순위로 고른 8188 MiB 카드를 Backend 가 "8192 에 4 MiB 모자란다"며
 * INSUFFICIENT 로 뒤집던 문제의 회귀 테스트다.
 */
class GpuFitPolicyTest {

    private static final int REQUIRED = 8192;

    @ParameterizedTest(name = "{0} MiB → nominal {1} MiB")
    @CsvSource({
            "8188, 8192",
            "8160, 8192",
            "8159, 8159",
            "8192, 8192",
            "8100, 8100",
            "24564, 24576",
            "0, 0",
    })
    @DisplayName("다음 1024 배수까지 32 MiB 이내면 그 배수로, 아니면 raw 그대로")
    void nominalVramMb(int raw, int nominal) {
        assertThat(GpuFitPolicy.nominalVramMb(raw)).isEqualTo(nominal);
    }

    @ParameterizedTest(name = "reported {0} / required 8192 → {1}")
    @CsvSource({
            "8188, false",
            "8160, false",
            "8159, true",
            "8192, false",
            "8100, true",
    })
    @DisplayName("실행 가능 여부는 nominal 기준이다")
    void feasibility(int reported, boolean insufficient) {
        Fit fit = GpuFitPolicy.fitOf(reported, REQUIRED, false);
        assertThat(fit == Fit.INSUFFICIENT).isEqualTo(insufficient);
    }

    @Test
    @DisplayName("Scheduler 가 1순위로 고른 8188 MiB 카드는 RECOMMENDED 다")
    void recommendedWinsOverRawShortfall() {
        assertThat(GpuFitPolicy.fitOf(8188, REQUIRED, true)).isEqualTo(Fit.RECOMMENDED);
        assertThat(GpuFitPolicy.reasonOf(Fit.RECOMMENDED, 8188, REQUIRED)).doesNotContain("부족");
    }

    @Test
    @DisplayName("TIGHT/OK 경계도 같은 nominal 기준을 쓴다 — INSUFFICIENT 만 고치면 경계가 다시 어긋난다")
    void tightAndOkUseNominal() {
        // 8192 * 1.25 = 10240. 10228 은 raw 로는 12 MiB 모자라지만 nominal 10240 이라 OK
        assertThat(GpuFitPolicy.fitOf(10228, REQUIRED, false)).isEqualTo(Fit.OK);
        // 10200 은 허용 오차 밖(40 MiB) — raw 그대로 비교해 TIGHT
        assertThat(GpuFitPolicy.fitOf(10200, REQUIRED, false)).isEqualTo(Fit.TIGHT);
    }

    @Test
    @DisplayName("부족량이 1 GB 미만이면 MiB 로 말한다 — \"0 GB 부족\" 은 나오지 않는다")
    void shortfallUnderOneGbIsInMib() {
        String reason = GpuFitPolicy.reasonOf(Fit.INSUFFICIENT, 7680, REQUIRED);
        assertThat(reason).isEqualTo("VRAM 이 512 MiB 부족해 실행할 수 없습니다.");
        assertThat(reason).doesNotContain("0 GB");
    }

    @Test
    @DisplayName("부족량이 1 GB 이상이면 GB 로 말한다")
    void shortfallOverOneGbIsInGb() {
        assertThat(GpuFitPolicy.reasonOf(Fit.INSUFFICIENT, 6144, REQUIRED))
                .isEqualTo("VRAM 이 2 GB 부족해 실행할 수 없습니다.");
    }

    @Test
    @DisplayName("여유 문구도 같은 단위 규칙이다")
    void freeSpaceUsesSameUnits() {
        assertThat(GpuFitPolicy.reasonOf(Fit.TIGHT, 8188, REQUIRED))
                .startsWith("여유 0 MiB 로 빠듯합니다.");
        assertThat(GpuFitPolicy.reasonOf(Fit.OK, 24564, REQUIRED))
                .isEqualTo("여유 16 GB 로 넉넉합니다.");
    }
}
