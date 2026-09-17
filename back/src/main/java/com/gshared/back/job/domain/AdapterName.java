package com.gshared.back.job.domain;

import java.util.Locale;
import java.util.regex.Pattern;

/**
 * 결과 어댑터 이름의 규칙. 이 이름은 그대로 <b>내려받을 파일 이름</b>이 되므로,
 * 경로 구분자·제어문자가 섞이면 저장이 위험해진다 — 그래서 문자 종류를 좁게 막는다.
 *
 * <p>규칙(프런트 {@code adapterNameSchema} 와 같다): 1~60자, 한글·영문·숫자·{@code - _ .}·공백만,
 * 시작은 한글·영숫자, 끝은 공백이 아닐 것.
 */
public final class AdapterName {

    public static final int MAX_LENGTH = 60;
    private static final Pattern VALID = Pattern.compile(
            "^[가-힣a-zA-Z0-9](?:[가-힣a-zA-Z0-9 ._-]*[가-힣a-zA-Z0-9._-])?$");

    private AdapterName() {
    }

    /** 앞뒤 공백을 떼고 본 값. 빈 문자열은 "안 보냄"과 같이 다룬다. */
    public static String normalize(String raw) {
        if (raw == null) {
            return null;
        }
        String trimmed = raw.trim();
        return trimmed.isEmpty() ? null : trimmed;
    }

    public static boolean isValid(String normalized) {
        return normalized != null && normalized.length() <= MAX_LENGTH && VALID.matcher(normalized).matches();
    }

    /**
     * 사용자가 이름을 안 보냈을 때의 기본값. 베이스 모델 slug 에서 만든다 — 프런트가 화면에 제안하는 값과 같은 모양이라,
     * 프런트가 그대로 보내든 비워 보내든 결과가 같다.
     */
    public static String defaultFor(String baseModelSlug) {
        String slug = baseModelSlug == null ? "" : baseModelSlug.toLowerCase(Locale.ROOT)
                .replaceAll("[^가-힣a-zA-Z0-9._-]+", "-")
                .replaceAll("^[._-]+|[._-]+$", "");
        if (slug.length() > MAX_LENGTH - "-lora".length()) {
            slug = slug.substring(0, MAX_LENGTH - "-lora".length());
        }
        return (slug.isEmpty() ? "adapter" : slug) + "-lora";
    }
}
