package com.gshared.back.workload.dto.response;

import com.gshared.back.workload.domain.Workload;
import com.gshared.back.workload.domain.WorkloadKind;

/**
 * 메뉴판 한 줄. 사용자가 고르는 데 필요한 것만 — 이미지 지문이나 최소 VRAM 같은
 * 내부 값은 내보내지 않는다(Scheduler 로 가는 값이지 사람이 보는 값이 아니다).
 */
public record WorkloadResponse(
        Long id,
        WorkloadKind kind,
        String name,
        String description
) {
    public static WorkloadResponse from(Workload workload) {
        return new WorkloadResponse(
                workload.getId(),
                workload.getKind(),
                workload.getName(),
                // 설명이 비어 있어도 화면이 깨지지 않게 빈 문자열로 — null 은 프론트 스키마가 거절한다.
                workload.getDescription() == null ? "" : workload.getDescription()
        );
    }
}
