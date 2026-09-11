package com.gshared.back.job.domain;

import com.gshared.back.global.blockchain.ExternalRef;
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
    // 온체인 전송 시에만 TokenUnits.toWei 로 변환하고, 체인에서 읽은 uint256은 long이 아니라 BigInteger로 받는다.
    // 아래 actualCost 도 같은 규칙이다.
    @Column(nullable = false)
    private Long estimatedCost;
    private Double actualRuntimeSec;        // JOB_RESULT/FAILED/CANCELLED 가 실어 오는 실측치 — AI 재학습 데이터
    private Long actualCost;

    // ── 에스크로 (규격 §11 escrow) ─────────────────────────────
    // 온체인 createAndFundJob(externalRef, price, provider) 에 넣는 참조값. 생성 시 발급, 이후 불변.
    @Column(length = 66, unique = true)
    private String externalRef;
    // 온체인 jobId (uint256). 예치 확인 때 기록. JSON 에서는 문자열 — JS 정수 정밀도 문제 회피.
    @Column(length = 78)
    private String escrowJobId;

    @Column(nullable = false)
    @Enumerated(EnumType.STRING)
    private JobStatus status;

    private String assignedWorkerId;  // 배정 확정된 Worker
    @Column(length = 66)
    private String acceptTxHash;      // acceptJob() 트랜잭션 (JOB_ACCEPTED)
    private String resultHash;        // 0x + SHA-256 — submitResult() 에 넣은 bytes32
    @Column(length = 512)
    private String resultObjectKey;   // 스토리지 오브젝트 키 (jobs/{id}/result.tar)
    private Long resultSizeBytes;
    @Column(length = 64)
    private String resultSha256;      // 업로드 완료 검증 때 Backend 가 확인한 파일 해시 (0x 없음)
    private String resultUrl;
    @Column(length = 66)
    private String submitTxHash;      // submitResult() 트랜잭션 (JOB_RESULT)
    @Column(length = 66)
    private String abortTxHash;       // abortJob() 트랜잭션 (JOB_FAILED / JOB_CANCELLED)
    @Column(columnDefinition = "TEXT")
    private String failReason;        // 실패·거절 시 에러코드+메시지 (긴 에러 대비 TEXT)
    // 온체인 상태와 DB 가 어긋났을 가능성 — 값이 있으면 자동 진행을 멈추고(fail closed) 대조가 필요하다는 표시
    @Column(columnDefinition = "TEXT")
    private String reconciliationReason;

    // 진행률 스냅샷 (JOB_PROGRESS 수신 시 갱신) — progressPercent = currentStep / totalSteps × 100
    private Double progressPercent;
    private Integer progressStep;
    private Integer progressTotalSteps;
    private Integer progressEpoch;
    private Integer progressTotalEpochs;
    private Integer progressElapsedSec;

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
        this.externalRef = ExternalRef.generate();   // 예치 트랜잭션에 실릴 참조값 — 견적 시점에 미리 발급
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

    /**
     * 결과물 업로드 완료 검증 기록 (규격 §18). 상태 전이가 아니라 "파일이 실제로 올라왔다"는 사실만 적는다 —
     * 완료(COMPLETED) 전이는 submitResult() 까지 끝난 뒤 JOB_RESULT 가 조건부 UPDATE 로 한다.
     */
    public void recordResultUpload(String objectKey, Long sizeBytes, String sha256) {
        this.resultObjectKey = objectKey;
        this.resultSizeBytes = sizeBytes;
        this.resultSha256 = sha256;
    }

    /**
     * 온체인 대조 필요 표시. 워커가 보고한 값(escrowJobId·operator·resultHash)이 Backend 기록과 다르거나,
     * 거절로 DB 는 끝났는데 온체인 에스크로는 아직 살아 있는 경우처럼 "둘 중 하나는 틀렸다"는 상황에 남긴다.
     * 자동 재매칭·자동 전이는 하지 않고, 온체인 상태를 읽어 정리하는 후속 작업의 입력이 된다.
     */
    public void flagReconciliation(String reason) {
        this.reconciliationReason = reason;
    }

    // 상태 전이(취소·배정·시작·완료·실패·거절)는 경쟁 조건 방지를 위해
    // 전부 JobRepository 의 조건부 UPDATE(...IfXxx)로만 수행한다.
}
