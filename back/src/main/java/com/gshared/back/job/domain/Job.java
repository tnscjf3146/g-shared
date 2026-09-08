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

    // 참조 ID (해당 도메인 테이블 추후 생성 → 지금은 값만 보관)
    @Column(nullable = false)
    private Long workloadId;
    @Column(nullable = false)
    private Long baseModelId;
    @Column(nullable = false)
    private Long datasetId;
    @Column(nullable = false)
    private Long gpuId;

    @Column(nullable = false, length = 10)
    private String trainingMode;      // AUTO / CUSTOM

    @Column(columnDefinition = "TEXT")
    private String trainingConfig;    // CUSTOM 학습설정 JSON (없으면 null)

    @Column(nullable = false)
    private Integer predictedRuntimeSec;
    @Column(nullable = false)
    private Long estimatedCost;
    private Integer actualRuntimeSec;
    private Long actualCost;

    @Column(nullable = false)
    @Enumerated(EnumType.STRING)
    private JobStatus status;

    private String assignedWorkerId;  // 배정된 Worker
    private String resultHash;
    private String resultUrl;
    private String failReason;        // 실패 시 에러코드+메시지 (GPU_OOM 등)

    // 진행률 스냅샷 (JOB_PROGRESS 수신 시 갱신)
    private Integer progressPercent;
    private Integer progressEpoch;
    private Integer progressTotalEpochs;

    private LocalDateTime startedAt;
    private LocalDateTime finishedAt;

    private Job(User owner, Long workloadId, Long baseModelId, Long datasetId, Long gpuId,
                String trainingMode, String trainingConfig,
                Integer predictedRuntimeSec, Long estimatedCost) {
        this.owner = owner;
        this.workloadId = workloadId;
        this.baseModelId = baseModelId;
        this.datasetId = datasetId;
        this.gpuId = gpuId;
        this.trainingMode = trainingMode;
        this.trainingConfig = trainingConfig;
        this.predictedRuntimeSec = predictedRuntimeSec;
        this.estimatedCost = estimatedCost;
        this.status = JobStatus.CREATED;   // 생성 직후엔 예치 확인 대기
    }

    public static Job create(User owner, Long workloadId, Long baseModelId, Long datasetId, Long gpuId,
                             String trainingMode, String trainingConfig,
                             Integer predictedRuntimeSec, Long estimatedCost) {
        return new Job(owner, workloadId, baseModelId, datasetId, gpuId,
                trainingMode, trainingConfig, predictedRuntimeSec, estimatedCost);
    }

    // 취소 전이는 경쟁 조건 방지를 위해 JobRepository.cancelIfCancellable(조건부 UPDATE)로만 수행한다.

    /** 매칭: Worker 배정 → ASSIGNED. (FUNDED 상태에서만 호출됨 — JobMatcher가 보장) */
    public void assign(String workerId) {
        this.assignedWorkerId = workerId;
        this.status = JobStatus.ASSIGNED;
    }
}
