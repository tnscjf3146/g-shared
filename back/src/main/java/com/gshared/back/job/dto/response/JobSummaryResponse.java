package com.gshared.back.job.dto.response;

import com.gshared.back.job.domain.Job;
import com.gshared.back.job.domain.JobStatus;

import java.time.LocalDateTime;

/**
 * 목록의 한 줄. 대시보드 리스트용이라 요약 정보만.
 */
public record JobSummaryResponse(
        Long jobId,
        JobStatus status,
        Long workloadId,
        Double progressPercent,   // currentStep / totalSteps × 100 — 규격상 소수
        Long estimatedCost,
        String failReason,        // 실패 흔적 — CANCELLING/CANCELLED 이어도 이 값이 있으면 화면은 "실패"로 구분한다
        String adapterName,       // "이어서 학습" 목록이 목록 API 하나로 "이름 v2" 를 보여 줄 수 있게
        Integer adapterVersion,
        LocalDateTime createdAt
) {
    public static JobSummaryResponse from(Job job) {
        return new JobSummaryResponse(
                job.getId(),
                job.getStatus(),
                job.getWorkloadId(),
                job.getProgressPercent(),
                job.getEstimatedCost(),
                job.getFailReason(),
                job.getAdapterName(),
                job.getAdapterVersion(),
                job.getCreatedAt()
        );
    }
}
