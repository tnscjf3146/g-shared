package com.gshared.back.job.service;

import com.gshared.back.job.dto.response.GpuOptionResponse.Fit;

/**
 * GPU 등급 적합도 판정이 있는 단 한 곳.
 *
 * <p>드라이버는 "8 GB" 카드를 8188 MiB 처럼 몇 MiB 모자라게 보고한다. Scheduler 는 이 값을
 * 1024 배수로 올려(nominal capacity) 실행 가능 여부를 판단하는데, 여기서 raw 값을 다시 비교하면
 * Scheduler 가 1순위로 고른 GPU 를 우리가 INSUFFICIENT 라고 말하는 모순이 생긴다.
 * 그래서 비교에만 nominal 값을 쓴다. 응답의 {@code vramMb} 는 raw 그대로다 — telemetry 를
 * 고치는 게 아니라 판정 기준을 Scheduler 와 맞추는 것이다.
 */
public final class GpuFitPolicy {

    /** nominal capacity 의 단위. 카드는 GiB 단위로 팔린다. */
    static final int STEP_MB = 1024;
    /** 다음 1024 배수까지 이만큼 이내로 모자라면 그 배수로 본다. Scheduler 와 같은 값. */
    static final int TOLERANCE_MB = 32;
    /** 필요 VRAM 대비 이만큼은 넘어야 "여유 있다"고 본다. 1.25 = 25% 여유. */
    private static final double COMFORTABLE_VRAM_RATIO = 1.25;

    private GpuFitPolicy() {
    }

    /**
     * 비교용 VRAM. 8188 → 8192, 8160 → 8192, 8159 → 8159, 8192 → 8192, 8100 → 8100.
     */
    public static int nominalVramMb(int rawVramMb) {
        int remainder = rawVramMb % STEP_MB;
        if (remainder == 0) {
            return rawVramMb;
        }
        int shortfall = STEP_MB - remainder;
        return shortfall <= TOLERANCE_MB ? rawVramMb + shortfall : rawVramMb;
    }

    /** 모든 VRAM 비교는 nominal 기준이다 — INSUFFICIENT 만 고치면 TIGHT/OK 경계가 다시 어긋난다. */
    public static Fit fitOf(int rawVramMb, int requiredVramMb, boolean recommended) {
        int vramMb = nominalVramMb(rawVramMb);
        if (vramMb < requiredVramMb) {
            return Fit.INSUFFICIENT;
        }
        if (recommended) {
            return Fit.RECOMMENDED;
        }
        return vramMb < requiredVramMb * COMFORTABLE_VRAM_RATIO ? Fit.TIGHT : Fit.OK;
    }

    /** 화면에 그대로 나가는 한 줄. 왜 이 판정인지를 숫자로 말한다. */
    public static String reasonOf(Fit fit, int rawVramMb, int requiredVramMb) {
        int vramMb = nominalVramMb(rawVramMb);
        return switch (fit) {
            case RECOMMENDED -> "예상 실행 시간이 가장 짧습니다.";
            case OK -> "여유 " + format(vramMb - requiredVramMb) + " 로 넉넉합니다.";
            case TIGHT -> "여유 " + format(vramMb - requiredVramMb) + " 로 빠듯합니다. 설정에 따라 실패할 수 있습니다.";
            case INSUFFICIENT -> "VRAM 이 " + format(requiredVramMb - vramMb) + " 부족해 실행할 수 없습니다.";
        };
    }

    /**
     * 1 GB 미만은 MiB 로 말한다. GB 로 반올림하면 "0 GB 부족" 같은 말이 안 되는 문장이 나온다.
     */
    static String format(int mb) {
        if (mb < STEP_MB) {
            return mb + " MiB";
        }
        return Math.round(mb / (float) STEP_MB) + " GB";
    }
}
