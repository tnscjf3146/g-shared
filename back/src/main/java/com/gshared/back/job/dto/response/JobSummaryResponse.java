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
        LocalDateTime createdAt
) {
    public static JobSummaryResponse from(Job job) {
        return new JobSummaryResponse(
                job.getId(),
                job.getStatus(),
                job.getWorkloadId(),
                job.getProgressPercent(),
                job.getEstimatedCost(),
                job.getCreatedAt()
        );
    }
}
