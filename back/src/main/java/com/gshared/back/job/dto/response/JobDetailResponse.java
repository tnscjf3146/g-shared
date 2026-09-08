package com.gshared.back.job.dto.response;

import com.gshared.back.job.domain.Job;
import com.gshared.back.job.domain.JobStatus;

import java.time.LocalDateTime;

/**
 * Job 단건 상세 응답. 대시보드에서 진행 상황 확인용.
 */
public record JobDetailResponse(
        Long jobId,
        JobStatus status,
        Long workloadId,
        Long baseModelId,
        Long gpuId,
        String trainingMode,
        Integer predictedRuntimeSec,
        Integer actualRuntimeSec,
        Long estimatedCost,
        Long actualCost,
        Integer progressPercent,
        Integer progressEpoch,
        Integer progressTotalEpochs,
        String resultHash,
        String resultUrl,
        LocalDateTime createdAt,
        LocalDateTime startedAt,
        LocalDateTime finishedAt
) {
    public static JobDetailResponse from(Job job) {
        return new JobDetailResponse(
                job.getId(),
                job.getStatus(),
                job.getWorkloadId(),
                job.getBaseModelId(),
                job.getGpuId(),
                job.getTrainingMode(),
                job.getPredictedRuntimeSec(),
                job.getActualRuntimeSec(),
                job.getEstimatedCost(),
                job.getActualCost(),
                job.getProgressPercent(),
                job.getProgressEpoch(),
                job.getProgressTotalEpochs(),
                job.getResultHash(),
                job.getResultUrl(),
                job.getCreatedAt(),
                job.getStartedAt(),
                job.getFinishedAt()
        );
    }
}
