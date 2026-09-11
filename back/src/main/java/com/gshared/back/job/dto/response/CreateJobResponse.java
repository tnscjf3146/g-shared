package com.gshared.back.job.dto.response;

import com.gshared.back.job.domain.Job;

/**
 * Job 생성 응답 = 견적서.
 * CREATED 상태의 Job 행이 곧 견적 — 사용자는 이 응답을 보고 예치(승인) 여부를 결정한다.
 *
 * <p>externalRef 는 예치 트랜잭션 createAndFundJob(externalRef, price, provider) 에 그대로 넣는 값이다.
 * price 는 estimatedCost(TOKEN)를 wei 로 바꿔 넣고, provider 는 추천 워커 주인의 지갑이다.
 */
public record CreateJobResponse(
        Long jobId,
        String recommendedWorkerId,
        Double predictedRuntimeSec,
        Long estimatedCost,
        String schedulerModelVersion,
        String externalRef
) {
    public static CreateJobResponse from(Job job) {
        return new CreateJobResponse(
                job.getId(),
                job.getRecommendedWorkerId(),
                job.getPredictedRuntimeSec(),
                job.getEstimatedCost(),
                job.getSchedulerModelVersion(),
                job.getExternalRef()
        );
    }
}
