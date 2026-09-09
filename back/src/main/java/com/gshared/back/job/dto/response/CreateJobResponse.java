package com.gshared.back.job.dto.response;

import com.gshared.back.job.domain.Job;

/**
 * Job 생성 응답 = 견적서.
 * CREATED 상태의 Job 행이 곧 견적 — 사용자는 이 응답을 보고 예치(승인) 여부를 결정한다.
 */
public record CreateJobResponse(
        Long jobId,
        String recommendedWorkerId,
        Double predictedRuntimeSec,
        Long estimatedCost,
        String schedulerModelVersion
) {
    public static CreateJobResponse from(Job job) {
        return new CreateJobResponse(
                job.getId(),
                job.getRecommendedWorkerId(),
                job.getPredictedRuntimeSec(),
                job.getEstimatedCost(),
                job.getSchedulerModelVersion()
        );
    }
}
