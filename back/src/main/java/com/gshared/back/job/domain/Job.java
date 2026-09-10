package com.gshared.back.job.domain;

import com.gshared.back.global.event.BaseTimeEntity;
import com.gshared.back.user.domain.User;
import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

@Entity
@Getter
@Table(name = "jobs")
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class Job extends BaseTimeEntity {

    @Id
    @Column(name = "job_id")
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    // 주문한 사용자
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "owner_id", nullable = false)
    private User owner;

    // 카탈로그 참조 (정수 PK — Scheduler 규격과 동일, V8)
    @Column(nullable = false)
    private Long workloadId;          // workloads PK = Scheduler dockerImageId
    @Column(nullable = false)
    private Long baseModelId;         // base_models PK = Scheduler modelId
    @Column(nullable = false)
    private Long datasetId;

    @Column(nullable = false, length = 10)
    private String trainingMode;      // AUTO / CUSTOM — "누가 정했는가" 기록용

    // 학습 설정 4종 — AUTO/CUSTOM 모두 확정값을 항상 채운다 (Scheduler 필수 입력)
    @Column(nullable = false)
    private Integer epochs;
    @Column(nullable = false)
    private Integer batchSize;
    @Column(nullable = false)
    private Double learningRate;
    @Column(nullable = false)
    private Integer maxLength;

    // Scheduler 예측 결과 (DOUBLE — 정밀도 보존, 예측 오차 평가용)
    @Column(nullable = false)
    private Double predictedRuntimeSec;
    @Column(length = 50)
    private String schedulerModelVersion;   // 어느 모델이 예측했나 — 실측 대조·개선 평가용
    @Column(length = 64)
    private String recommendedWorkerId;     // 추천 ≠ 배정 확정 (배정은 assignedWorkerId)

    // 단위: TOKEN(GSC, 사람 단위). wei(×10^18)는 절대 저장하지 않는다 — BIGINT는 9 GSC에서 넘친다.
    // 온체인 전송 시에만 BigInteger.valueOf(cost).multiply(TEN.pow(18)) 로 변환하고,
    // 체인에서 읽은 uint256은 long이 아니라 BigInteger로 받는다. (03-에스크로-연동-규격.md)
    // 아래 actualCost 도 같은 규칙이다.
    @Column(nullable = false)
    private Long estimatedCost;
    private Double actualRuntimeSec;
    private Long actualCost;

    @Column(nullable = false)
    @Enumerated(EnumType.STRING)
    private JobStatus status;

    private String assignedWorkerId;  // 배정 확정된 Worker
    private String resultHash;
    private String resultUrl;
    @Column(columnDefinition = "TEXT")
    private String failReason;        // 실패 시 에러코드+메시지 (긴 에러 대비 TEXT)

    // 진행률 스냅샷 (JOB_PROGRESS 수신 시 갱신)
    private Integer progressPercent;
    private Integer progressEpoch;
    private Integer progressTotalEpochs;

    private LocalDateTime startedAt;
    private LocalDateTime finishedAt;

    private Job(User owner, Long workloadId, Long baseModelId, Long datasetId,
                String trainingMode, Integer epochs, Integer batchSize, Double learningRate, Integer maxLength,
                Double predictedRuntimeSec, String schedulerModelVersion, String recommendedWorkerId,
                Long estimatedCost) {
        this.owner = owner;
        this.workloadId = workloadId;
        this.baseModelId = baseModelId;
        this.datasetId = datasetId;
        this.trainingMode = trainingMode;
        this.epochs = epochs;
        this.batchSize = batchSize;
        this.learningRate = learningRate;
        this.maxLength = maxLength;
        this.predictedRuntimeSec = predictedRuntimeSec;
        this.schedulerModelVersion = schedulerModelVersion;
        this.recommendedWorkerId = recommendedWorkerId;
        this.estimatedCost = estimatedCost;
        this.status = JobStatus.CREATED;   // 생성 직후엔 예치 확인 대기 — 이 행이 곧 견적서
    }

    public static Job create(User owner, Long workloadId, Long baseModelId, Long datasetId,
                             String trainingMode, Integer epochs, Integer batchSize,
                             Double learningRate, Integer maxLength,
                             Double predictedRuntimeSec, String schedulerModelVersion,
                             String recommendedWorkerId, Long estimatedCost) {
        return new Job(owner, workloadId, baseModelId, datasetId,
                trainingMode, epochs, batchSize, learningRate, maxLength,
                predictedRuntimeSec, schedulerModelVersion, recommendedWorkerId, estimatedCost);
    }

    // 상태 전이(취소·배정·시작·완료·실패)는 경쟁 조건 방지를 위해
    // 전부 JobRepository 의 조건부 UPDATE(...IfXxx)로만 수행한다.
}
