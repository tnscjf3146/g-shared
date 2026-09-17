package com.gshared.back.job.service;

import com.gshared.back.global.storage.ObjectStorage;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Optional;
import java.util.Set;

/**
 * 입력 LoRA 어댑터 {@code .tar} 의 형식 검사 (lora-adapter-backend-작업 §0·§3).
 *
 * <p>tar 는 512바이트 헤더 + 내용(512 배수 패딩)의 반복이라, 헤더 블록만 건너뛰며 읽으면 목록을 알 수 있다.
 * 그래서 1 GiB 파일도 통째로 읽지 않고 {@link ObjectStorage#readRange} 로 헤더 위치만 집어 읽는다.
 *
 * <p>Worker·Runtime 도 같은 규칙으로 다시 검사한다. Backend 가 한 번 더 보는 이유는 신뢰 경계이기 때문이기도 하고,
 * 여기서 걸러야 사용자가 <b>예치 전에</b> 잘못된 파일을 알기 때문이다 — 예치 뒤에 실패하면 매칭·워커 시간이 낭비된다.
 *
 * <p>이름은 접두사 없이 정확히 일치해야 한다: {@code ./adapter_config.json} 도 거절한다. 완료 Job 의
 * {@code result.tar} 는 접두사 없이 만들어지므로 그대로 이어서 학습에 쓸 수 있다.
 */
final class AdapterTarFormat {

    /** 루트에 반드시 있어야 하는 항목. */
    private static final List<String> REQUIRED = List.of("adapter_config.json", "adapter_model.safetensors");
    /** 그 밖에 허용하는 항목 — 완료 Job 의 result.tar 를 그대로 넣을 수 있게. */
    private static final Set<String> ALLOWED = Set.of(
            "adapter_config.json", "adapter_model.safetensors", "training_metrics.json");
    private static final int BLOCK = 512;
    /** 헤더가 이보다 많으면 어댑터가 아니다 — 깨진 파일에서 무한히 읽지 않게 하는 안전장치. */
    private static final int MAX_ENTRIES = 64;

    private AdapterTarFormat() {
    }

    /**
     * 어댑터 tar 인지 확인한다.
     *
     * @return 문제가 없으면 empty, 있으면 로그에 남길 사유 (사용자에게는 고정 문구를 준다 — 내부 구조를 흘리지 않는다)
     */
    static Optional<String> validate(ObjectStorage storage, String objectKey, long sizeBytes) {
        if (sizeBytes % BLOCK != 0) {
            return Optional.of("크기가 512의 배수가 아님: " + sizeBytes);   // tar 는 항상 블록 단위다
        }
        var found = new java.util.HashSet<String>();
        long at = 0;
        for (int seen = 0; at + BLOCK <= sizeBytes; seen++) {
            if (seen >= MAX_ENTRIES) {
                return Optional.of("항목이 너무 많음(>" + MAX_ENTRIES + ")");
            }
            byte[] header = storage.readRange(objectKey, at, BLOCK).orElse(null);
            if (header == null || header.length < BLOCK) {
                return Optional.of("헤더를 읽지 못함 offset=" + at);
            }
            String name = trimNul(header, 0, 100);
            if (name.isEmpty()) {
                break;   // 널 블록 = 아카이브 끝
            }
            if (!ALLOWED.contains(name)) {
                return Optional.of("허용하지 않는 항목: " + name);
            }
            byte flag = header[156];
            if (flag != 0 && flag != '0') {
                return Optional.of("일반 파일이 아닌 항목: " + name + " typeflag=" + (char) flag);
            }
            long entrySize = parseOctal(trimNul(header, 124, 12));
            if (entrySize < 0) {
                return Optional.of("크기 필드가 깨짐: " + name);
            }
            found.add(name);
            at += BLOCK + ((entrySize + BLOCK - 1) / BLOCK) * BLOCK;
        }
        List<String> missing = REQUIRED.stream().filter(n -> !found.contains(n)).toList();
        return missing.isEmpty() ? Optional.empty() : Optional.of("필수 항목 없음: " + String.join(", ", missing));
    }

    /** tar 헤더의 문자열 필드는 NUL 로 끝난다(꽉 차면 NUL 없이 끝나기도 한다). */
    private static String trimNul(byte[] header, int offset, int length) {
        int end = offset;
        while (end < offset + length && header[end] != 0) {
            end++;
        }
        return new String(header, offset, end - offset, StandardCharsets.UTF_8).trim();
    }

    /** 8진수 문자열 → 값. 비어 있으면 0, 숫자가 아니면 -1. */
    private static long parseOctal(String text) {
        if (text.isEmpty()) {
            return 0;
        }
        try {
            return Long.parseLong(text, 8);
        } catch (NumberFormatException e) {
            return -1;
        }
    }
}
