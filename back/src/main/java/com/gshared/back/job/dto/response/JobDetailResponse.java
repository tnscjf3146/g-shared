package com.gshared.back.job.dto.response;

import com.gshared.back.global.blockchain.TokenUnits;
import com.gshared.back.job.domain.Job;
import com.gshared.back.job.domain.JobStatus;

import java.time.LocalDateTime;

/**
 * Job 단건 상세 응답. 대시보드에서 진행 상황·결과·실패 사유 확인용.
 * 에스크로 참조(externalRef·escrowJobId)와 트랜잭션 해시도 실어 화면이 탐색기 링크를 걸 수 있게 한다.
 *
 * <p>예치 입력값(priceWei·clientWallet·chainId·tokenAddress·escrowAddress)은 {@link CreateJobResponse} 와
 * 같은 이름·의미로 여기에도 싣는다 — 프런트가 상세 화면에서(새로고침 뒤에도) 예치를 이어갈 수 있게, 별도
 * escrow-terms 창구 없이 이 응답 하나로 끝낸다. chainId·tokenAddress·escrowAddress 는 <b>지금</b> 설정값이고,
 * 예치가 끝난 Job 의 실제 체인·컨트랙트는 escrowChainId·escrowContractAddress 에 따로 박혀 있다.
 */
public record JobDetailResponse(
        Long jobId,
        JobStatus status,
        Long workloadId,
        Long baseModelId,
        Long datasetId,
        Boolean datasetUploaded,       // 원본 파일이 올라와 검증됐는가 — false 면 예치 전에 dataset-upload 부터
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
        Long escrowChainId,
        String escrowContractAddress,
        String escrowJobId,
        String fundTxHash,
        String providerWallet,
        String priceWei,               // estimatedCost 의 wei 표현 — JS 정수 범위를 넘으므로 10진 문자열
        String clientWallet,           // 의뢰자 등록 지갑 — MetaMask 현재 계정과 대조용
        Long chainId,                  // 예치할 체인 (현재 설정값)
        String tokenAddress,           // GSC 토큰 (현재 설정값)
        String escrowAddress,          // 에스크로 컨트랙트 (현재 설정값, 체인 미설정이면 null)
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
    public static JobDetailResponse from(Job job, Long chainId, String tokenAddress, String escrowAddress,
                                         String resultDownloadUrl) {
        return new JobDetailResponse(
                job.getId(),
                job.getStatus(),
                job.getWorkloadId(),
                job.getBaseModelId(),
                job.getDatasetId(),
                job.hasDatasetFile(),
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
                job.getEscrowChainId(),
                job.getEscrowContractAddress(),
                job.getEscrowJobId(),
                job.getFundTxHash(),
                job.getProviderWallet(),
                TokenUnits.toWei(job.getEstimatedCost()).toString(),
                job.getOwner().getWalletAddress(),
                chainId,
                tokenAddress,
                escrowAddress,
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
                // 조회 시점에 발급한 서명 URL — 엔티티의 resultUrl 은 워커가 신고한 외부 URL 자리라 별개
                resultDownloadUrl != null ? resultDownloadUrl : job.getResultUrl(),
                job.getFailReason(),
                job.getCreatedAt(),
                job.getStartedAt(),
                job.getFinishedAt()
        );
    }
}
