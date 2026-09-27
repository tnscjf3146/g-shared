package com.gshared.back.workload.dto.response;

import com.gshared.back.workload.domain.BaseModel;

import java.util.List;

/**
 * 베이스 모델 한 줄.
 *
 * <p>{@code workloadIds} 가 배열인 것은 화면이 "이 모델을 쓸 수 있는 워크로드"로 거르기 때문이다.
 * 지금 저장 구조는 이미지 1 : 모델 N 이라 언제나 원소 하나지만, 배열로 내보내 두면
 * 나중에 한 모델을 여러 이미지에서 쓰게 되어도 화면을 고치지 않아도 된다.
 */
public record BaseModelResponse(
        Long id,
        String name,
        String params,
        List<Long> workloadIds
) {
    public static BaseModelResponse from(BaseModel model) {
        return new BaseModelResponse(
                model.getId(),
                model.getDisplayName(),
                formatParams(model.getParamCount()),
                List.of(model.getWorkload().getId())
        );
    }

    /**
     * 파라미터 수를 사람이 읽는 문자열로. 7000000000 → "7B".
     * 화면에서 자릿수를 세게 두지 않으려고 서버가 만들어 보낸다 — 표기가 바뀌면 여기만 고친다.
     */
    private static String formatParams(Long paramCount) {
        if (paramCount == null || paramCount <= 0) {
            return "";
        }
        if (paramCount >= 1_000_000_000L) {
            return trimZero(paramCount / 1_000_000_000.0) + "B";
        }
        if (paramCount >= 1_000_000L) {
            return trimZero(paramCount / 1_000_000.0) + "M";
        }
        return paramCount.toString();
    }

    /** 7.0B 가 아니라 7B 로 — 소수점이 의미 없을 때는 떼고 보낸다. */
    private static String trimZero(double value) {
        return value == Math.floor(value)
                ? String.valueOf((long) value)
                : String.valueOf(Math.round(value * 10) / 10.0);
    }
}
