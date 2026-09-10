package com.gshared.back.job.dto.response;

import com.gshared.back.job.domain.Job;
import com.gshared.back.job.domain.JobStatus;

import java.time.LocalDateTime;

/**
 * Job 단건 상세 응답. 대시보드에서 진행 상황·결과·실패 사유 확인용.
 */
public record JobDetailResponse(
        Long jobId,
        JobStatus status,
        Long workloadId,
        Long baseModelId,
        Long datasetId,
        String trainingMode,
        Integer epochs,
        Integer batchSize,
        Double learningRate,
        Integer maxLength,
        Double predictedRuntimeSec,
        Double actualRuntimeSec,
        Long estimatedCost,
        Long actualCost,
        String assignedWorkerId,
        Integer progressPercent,
        Integer progressEpoch,
        Integer progressTotalEpochs,
        String resultHash,
        String resultUrl,
        String failReason,
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
                job.getDatasetId(),
                job.getTrainingMode(),
                job.getEpochs(),
                job.getBatchSize(),
                job.getLearningRate(),
                job.getMaxLength(),
                job.getPredictedRuntimeSec(),
                job.getActualRuntimeSec(),
                job.getEstimatedCost(),
                job.getActualCost(),
                job.getAssignedWorkerId(),
                job.getProgressPercent(),
                job.getProgressEpoch(),
                job.getProgressTotalEpochs(),
                job.getResultHash(),
                job.getResultUrl(),
                job.getFailReason(),
                job.getCreatedAt(),
                job.getStartedAt(),
                job.getFinishedAt()
        );
    }
}
