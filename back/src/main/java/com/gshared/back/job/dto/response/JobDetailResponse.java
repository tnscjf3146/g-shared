package com.gshared.back.job.dto.response;

import com.gshared.back.job.domain.Job;
import com.gshared.back.job.domain.JobStatus;

import java.time.LocalDateTime;

/**
 * Job 단건 상세 응답. 대시보드에서 진행 상황·결과·실패 사유 확인용.
 * 에스크로 참조(externalRef·escrowJobId)와 트랜잭션 해시도 실어 화면이 탐색기 링크를 걸 수 있게 한다.
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
        String externalRef,
        String escrowJobId,
        String assignedWorkerId,
        String acceptTxHash,
        String submitTxHash,
        String abortTxHash,
        String reconciliationReason,   // 값이 있으면 온체인 대조 필요 — 자동 진행이 멈춘 Job
        Double progressPercent,
        Integer progressStep,
        Integer progressTotalSteps,
        Integer progressEpoch,
        Integer progressTotalEpochs,
        Integer progressElapsedSec,
        String resultHash,
        String resultObjectKey,
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
                job.getExternalRef(),
                job.getEscrowJobId(),
                job.getAssignedWorkerId(),
                job.getAcceptTxHash(),
                job.getSubmitTxHash(),
                job.getAbortTxHash(),
                job.getReconciliationReason(),
                job.getProgressPercent(),
                job.getProgressStep(),
                job.getProgressTotalSteps(),
                job.getProgressEpoch(),
                job.getProgressTotalEpochs(),
                job.getProgressElapsedSec(),
                job.getResultHash(),
                job.getResultObjectKey(),
                job.getResultUrl(),
                job.getFailReason(),
                job.getCreatedAt(),
                job.getStartedAt(),
                job.getFinishedAt()
        );
    }
}
