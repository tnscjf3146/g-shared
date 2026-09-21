package com.gshared.back.job.repository;

import com.gshared.back.job.domain.Job;
import com.gshared.back.job.domain.JobStatus;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.LocalDateTime;
import java.util.Optional;

public interface JobRepository extends JpaRepository<Job, Long> {

    // 본인 Job 목록 (페이지네이션) — 상태 필터 유무 두 가지
    Page<Job> findByOwnerId(Long ownerId, Pageable pageable);

    Page<Job> findByOwnerIdAndStatus(Long ownerId, JobStatus status, Pageable pageable);

    // 본인 Job 단건 (소유권 확인까지 한 번에)
    Optional<Job> findByIdAndOwnerId(Long id, Long ownerId);

    // 워커 전용 창구용: "이 워커에게 배정된" Job 단건. 남의 Job 은 애초에 안 잡힌다.
    Optional<Job> findByIdAndAssignedWorkerId(Long id, String assignedWorkerId);

    // 예치 트랜잭션 재사용 검사용. fund_tx_hash 는 UNIQUE 라 최대 한 건이다.
    Optional<Job> findByFundTxHash(String fundTxHash);

    /**
     * 매칭 순찰용: 배정 대기(FUNDED) Job을 오래된 순(선착순)으로.
     * owner 를 함께 읽는 이유는 JOB_ASSIGN 에 의뢰자 지갑(clientWallet)이 실리기 때문이다 —
     * 순찰은 트랜잭션 밖이라 LAZY 로 두면 접근 시점에 세션이 없다.
     */
    @Query("""
            SELECT j FROM Job j
              JOIN FETCH j.owner
             WHERE j.status = com.gshared.back.job.domain.JobStatus.FUNDED
             ORDER BY j.createdAt ASC
            """)
    java.util.List<Job> findFundedWithOwner();

    /**
     * 데이터셋 원본 cleanup 순찰용: 파일이 아직 스토리지에 있는데(업로드됨·미삭제) 더는 필요 없는 Job.
     * ① 끝난 Job(COMPLETED/FAILED/CANCELLED) — 워커는 이미 받아갔거나 받을 일이 없다.
     * ② 예치 없이 방치된 CREATED Job — createdAt 이 기준 시각보다 오래됐으면 버려진 견적으로 본다.
     */
    @Query("""
            SELECT j FROM Job j
             WHERE j.datasetUploadedAt IS NOT NULL
               AND j.datasetDeletedAt IS NULL
               AND (j.status IN (com.gshared.back.job.domain.JobStatus.COMPLETED,
                                 com.gshared.back.job.domain.JobStatus.FAILED,
                                 com.gshared.back.job.domain.JobStatus.CANCELLED)
                    OR (j.status = com.gshared.back.job.domain.JobStatus.CREATED
                        AND j.createdAt < :abandonedBefore))
            """)
    java.util.List<Job> findDatasetFilesToClean(@Param("abandonedBefore") LocalDateTime abandonedBefore);

    /** 같은 조건의 어댑터 판 — 끝난 Job 과 예치 없이 방치된 CREATED Job 의 입력 어댑터(V17). */
    @Query("""
            SELECT j FROM Job j
             WHERE j.adapterUploadedAt IS NOT NULL
               AND j.adapterDeletedAt IS NULL
               AND (j.status IN (com.gshared.back.job.domain.JobStatus.COMPLETED,
                                 com.gshared.back.job.domain.JobStatus.FAILED,
                                 com.gshared.back.job.domain.JobStatus.CANCELLED)
                    OR (j.status = com.gshared.back.job.domain.JobStatus.CREATED
                        AND j.createdAt < :abandonedBefore))
            """)
    java.util.List<Job> findAdapterFilesToClean(@Param("abandonedBefore") LocalDateTime abandonedBefore);

    /**
     * 원자적 취소 ①: 아직 워커 손에 가지 않은 Job(CREATED/FUNDED)은 바로 CANCELLED.
     * 확인→변경 사이 틈(경쟁 조건)이 없고, 동시 이중 취소도 한쪽만 성공(영향 행 수로 판별).
     * @return 영향받은 행 수 (1=취소 성공, 0=상태가 이미 바뀜 — ②를 시도하거나 409)
     */
    @Modifying(clearAutomatically = true)
    @Query("""
            UPDATE Job j
               SET j.status = com.gshared.back.job.domain.JobStatus.CANCELLED,
                   j.cancelReason = 'USER_REQUEST',
                   j.finishedAt = :now
             WHERE j.id = :jobId
               AND j.owner.id = :ownerId
               AND j.status IN (com.gshared.back.job.domain.JobStatus.CREATED,
                                com.gshared.back.job.domain.JobStatus.FUNDED)
            """)
    int cancelIfUnassigned(@Param("jobId") Long jobId, @Param("ownerId") Long ownerId, @Param("now") LocalDateTime now);

    /**
     * 원자적 취소 ②: 워커 손에 있는 Job(ASSIGNED/RUNNING)은 CANCELLING 으로만 바꾼다.
     * 워커가 컨테이너를 멈추고 abortJob() 한 뒤 JOB_CANCELLED 로 답해야 CANCELLED 로 확정된다 —
     * 워커 응답을 보장된 것으로 보지 않으므로, 답이 없으면 CANCELLING 에 남아 온체인 대조 대상이 된다.
     */
    @Modifying(clearAutomatically = true)
    @Query("""
            UPDATE Job j
               SET j.status = com.gshared.back.job.domain.JobStatus.CANCELLING,
                   j.cancelReason = 'USER_REQUEST'
             WHERE j.id = :jobId
               AND j.owner.id = :ownerId
               AND j.status IN (com.gshared.back.job.domain.JobStatus.ASSIGNED,
                                com.gshared.back.job.domain.JobStatus.RUNNING)
            """)
    int markCancellingIfActive(@Param("jobId") Long jobId, @Param("ownerId") Long ownerId);

    /**
     * Backend 가 fail closed 로 멈출 때: "이 워커에게 ASSIGNED" 인 Job 을 CANCELLING 으로.
     * JOB_ACCEPTED 의 escrowJobId·operatorAddress 가 기록과 다를 때 RUNNING 대신 이쪽으로 보내고
     * 워커에게 JOB_CANCEL 을 보낸다 (온체인은 이미 accept 됐을 수 있어 abortJob 이 필요하다).
     */
    @Modifying(clearAutomatically = true)
    @Query("""
            UPDATE Job j
               SET j.status = com.gshared.back.job.domain.JobStatus.CANCELLING,
                   j.cancelReason = 'SYSTEM_RECONCILED'
             WHERE j.id = :jobId
               AND j.assignedWorkerId = :workerId
               AND j.status = com.gshared.back.job.domain.JobStatus.ASSIGNED
            """)
    int markCancellingIfAssigned(@Param("jobId") Long jobId, @Param("workerId") String workerId);

    /**
     * JOB_CANCELLED 수신: CANCELLING 인 "이 워커의 Job" 을 CANCELLED 로 확정 + abortJob() 흔적·실측 시간 기록.
     */
    @Modifying(clearAutomatically = true)
    @Query("""
            UPDATE Job j
               SET j.status = com.gshared.back.job.domain.JobStatus.CANCELLED,
                   j.abortTxHash = :abortTxHash,
                   j.actualRuntimeSec = :actualRuntimeSec,
                   j.finishedAt = :now
             WHERE j.id = :jobId
               AND j.assignedWorkerId = :workerId
               AND j.status = com.gshared.back.job.domain.JobStatus.CANCELLING
            """)
    int confirmCancelledIfCancelling(@Param("jobId") Long jobId, @Param("workerId") String workerId,
                                     @Param("abortTxHash") String abortTxHash,
                                     @Param("actualRuntimeSec") Double actualRuntimeSec,
                                     @Param("now") LocalDateTime now);

    /**
     * 원자적 시작(JOB_ACCEPTED): "이 워커에게 배정된 ASSIGNED 상태일 때만" RUNNING 전이 + acceptJob() 해시 기록.
     * 엉뚱한 워커의 메시지·중복 메시지는 조건 불일치로 0행 → 무시된다.
     */
    @Modifying(clearAutomatically = true)
    @Query("""
            UPDATE Job j
               SET j.status = com.gshared.back.job.domain.JobStatus.RUNNING,
                   j.acceptTxHash = :acceptTxHash,
                   j.startedAt = :now
             WHERE j.id = :jobId
               AND j.assignedWorkerId = :workerId
               AND j.status = com.gshared.back.job.domain.JobStatus.ASSIGNED
            """)
    int startIfAssigned(@Param("jobId") Long jobId, @Param("workerId") String workerId,
                        @Param("acceptTxHash") String acceptTxHash, @Param("now") LocalDateTime now);

    /**
     * JOB_REJECTED (영구 사유): "이 워커에게 배정된 ASSIGNED 상태일 때만" FAILED 전이 + 사유 기록.
     * 워커가 acceptJob 전 검증(가격·주소·상태 불일치)에서 거절한 것이라 다시 배정해도 같은 결과다.
     */
    @Modifying(clearAutomatically = true)
    @Query("""
            UPDATE Job j
               SET j.status = com.gshared.back.job.domain.JobStatus.FAILED,
                   j.failReason = :failReason,
                   j.finishedAt = :now
             WHERE j.id = :jobId
               AND j.assignedWorkerId = :workerId
               AND j.status = com.gshared.back.job.domain.JobStatus.ASSIGNED
            """)
    int rejectIfAssigned(@Param("jobId") Long jobId, @Param("workerId") String workerId,
                         @Param("failReason") String failReason, @Param("now") LocalDateTime now);

    /** JOB_PROGRESS: "이 워커의 RUNNING Job"일 때만 진행률 갱신. */
    @Modifying(clearAutomatically = true)
    @Query("""
            UPDATE Job j
               SET j.progressPercent = :percent,
                   j.progressStep = :step,
                   j.progressTotalSteps = :totalSteps,
                   j.progressEpoch = :epoch,
                   j.progressTotalEpochs = :totalEpochs,
                   j.progressElapsedSec = :elapsedSec
             WHERE j.id = :jobId
               AND j.assignedWorkerId = :workerId
               AND j.status = com.gshared.back.job.domain.JobStatus.RUNNING
            """)
    int updateProgressIfRunning(@Param("jobId") Long jobId, @Param("workerId") String workerId,
                                @Param("percent") Double percent,
                                @Param("step") Integer step, @Param("totalSteps") Integer totalSteps,
                                @Param("epoch") Integer epoch, @Param("totalEpochs") Integer totalEpochs,
                                @Param("elapsedSec") Integer elapsedSec);

    /** JOB_RESULT: "이 워커의 RUNNING Job"일 때만 COMPLETED 전이 + 결과·submitResult() 해시 기록. */
    @Modifying(clearAutomatically = true)
    @Query("""
            UPDATE Job j
               SET j.status = com.gshared.back.job.domain.JobStatus.COMPLETED,
                   j.resultObjectKey = :resultObjectKey,
                   j.resultHash = :resultHash,
                   j.submitTxHash = :submitTxHash,
                   j.actualRuntimeSec = :actualRuntimeSec,
                   j.finishedAt = :now
             WHERE j.id = :jobId
               AND j.assignedWorkerId = :workerId
               AND j.status = com.gshared.back.job.domain.JobStatus.RUNNING
            """)
    int completeIfRunning(@Param("jobId") Long jobId, @Param("workerId") String workerId,
                          @Param("resultObjectKey") String resultObjectKey, @Param("resultHash") String resultHash,
                          @Param("submitTxHash") String submitTxHash,
                          @Param("actualRuntimeSec") Double actualRuntimeSec, @Param("now") LocalDateTime now);

    /**
     * JOB_RESULT 가 <b>취소 요청과 겹쳐</b> 도착한 경우: CANCELLING → COMPLETED.
     *
     * <p>사용자가 취소를 누른 순간 워커는 이미 결과를 올리고 온체인 submitResult 까지
     * 끝냈을 수 있다. 그러면 에스크로는 COMPLETED 이고 공급자는 이미 정산을 받았는데,
     * {@link #completeIfRunning} 은 RUNNING 만 받으므로 그 JOB_RESULT 는 버려지고
     * Job 은 CANCELLING 에 영원히 남는다 — 돈은 지불됐고 기록은 취소 중인 상태.
     *
     * <p><b>무조건 완료시키지 않는다.</b> 호출 전에 {@code JobService.completeJob} 이
     * §18 업로드 검증 기록(objectKey · resultSha256)과 워커가 보고한 resultHash 를
     * 대조하고, 하나라도 어긋나면 여기까지 오지 않는다. 즉 이 전이는 "워커가 그렇게
     * 말했다"가 아니라 "Backend 가 직접 해시를 계산해 확인한 결과물이 있다"에 근거한다.
     */
    @Modifying(clearAutomatically = true)
    @Query("""
            UPDATE Job j
               SET j.status = com.gshared.back.job.domain.JobStatus.COMPLETED,
                   j.resultObjectKey = :resultObjectKey,
                   j.resultHash = :resultHash,
                   j.submitTxHash = :submitTxHash,
                   j.actualRuntimeSec = :actualRuntimeSec,
                   j.reconciliationReason = :reconciliationReason,
                   j.finishedAt = :now
             WHERE j.id = :jobId
               AND j.assignedWorkerId = :workerId
               AND j.status = com.gshared.back.job.domain.JobStatus.CANCELLING
            """)
    int completeIfCancelling(@Param("jobId") Long jobId, @Param("workerId") String workerId,
                             @Param("resultObjectKey") String resultObjectKey, @Param("resultHash") String resultHash,
                             @Param("submitTxHash") String submitTxHash,
                             @Param("actualRuntimeSec") Double actualRuntimeSec,
                             @Param("reconciliationReason") String reconciliationReason,
                             @Param("now") LocalDateTime now);

    /**
     * JOB_FAILED 가 취소 요청과 겹쳐 도착한 경우: CANCELLING → <b>CANCELLED</b>.
     *
     * <p>FAILED 가 아니라 CANCELLED 인 이유는 온체인 결과가 그렇기 때문이다. 워커가
     * 실패를 보고했다는 것은 abortJob 으로 예치금을 돌려줬다는 뜻이고 — 규격 §20 의
     * abortTxHash 가 그 증거다 — 체인은 CANCELLED 다. 사용자는 취소를 요청했고 돈은
     * 돌아갔으므로 CANCELLED 가 양쪽과 맞는 유일한 종료 상태다.
     *
     * <p>실패 사유는 버리지 않고 failReason 에 그대로 남긴다. 취소로 끝났다는 것과
     * 그 전에 무엇이 잘못됐는지는 다른 정보다.
     */
    @Modifying(clearAutomatically = true)
    @Query("""
            UPDATE Job j
               SET j.status = com.gshared.back.job.domain.JobStatus.CANCELLED,
                   j.failReason = :failReason,
                   j.abortTxHash = :abortTxHash,
                   j.actualRuntimeSec = :actualRuntimeSec,
                   j.reconciliationReason = :reconciliationReason,
                   j.finishedAt = :now
             WHERE j.id = :jobId
               AND j.assignedWorkerId = :workerId
               AND j.status = com.gshared.back.job.domain.JobStatus.CANCELLING
            """)
    int cancelIfFailedWhileCancelling(@Param("jobId") Long jobId, @Param("workerId") String workerId,
                                      @Param("failReason") String failReason,
                                      @Param("abortTxHash") String abortTxHash,
                                      @Param("actualRuntimeSec") Double actualRuntimeSec,
                                      @Param("reconciliationReason") String reconciliationReason,
                                      @Param("now") LocalDateTime now);

    /** JOB_FAILED: "이 워커의 RUNNING Job"일 때만 FAILED 전이 + 사유·abortJob() 해시 기록. */
    @Modifying(clearAutomatically = true)
    @Query("""
            UPDATE Job j
               SET j.status = com.gshared.back.job.domain.JobStatus.FAILED,
                   j.failReason = :failReason,
                   j.abortTxHash = :abortTxHash,
                   j.actualRuntimeSec = :actualRuntimeSec,
                   j.finishedAt = :now
             WHERE j.id = :jobId
               AND j.assignedWorkerId = :workerId
               AND j.status = com.gshared.back.job.domain.JobStatus.RUNNING
            """)
    int failIfRunning(@Param("jobId") Long jobId, @Param("workerId") String workerId,
                      @Param("failReason") String failReason, @Param("abortTxHash") String abortTxHash,
                      @Param("actualRuntimeSec") Double actualRuntimeSec, @Param("now") LocalDateTime now);

    /** 실패 보고를 환불 순찰에 남긴다. 이미 취소 중이어도 실패 원인을 기록한다. */
    @Modifying(clearAutomatically = true)
    @Query("""
            UPDATE Job j
               SET j.status = com.gshared.back.job.domain.JobStatus.CANCELLING,
                   j.failReason = COALESCE(j.failReason, :failReason),
                   j.actualRuntimeSec = COALESCE(j.actualRuntimeSec, :actualRuntimeSec),
                   j.reconciliationReason = :reconciliationReason
             WHERE j.id = :jobId
               AND j.assignedWorkerId = :workerId
               AND j.status IN (com.gshared.back.job.domain.JobStatus.RUNNING,
                                com.gshared.back.job.domain.JobStatus.CANCELLING)
            """)
    int holdFailureForRefund(@Param("jobId") Long jobId, @Param("workerId") String workerId,
                             @Param("failReason") String failReason,
                             @Param("actualRuntimeSec") Double actualRuntimeSec,
                             @Param("reconciliationReason") String reconciliationReason);

    /**
     * FUNDED 취소인데 환불이 체인에서 확인되지 않은 경우: FUNDED → <b>CANCELLING</b>.
     * holdFailureForRefund 와 같은 원칙 — 돈이 잠긴 한 종료(CANCELLED)로 문을 닫지 않고
     * keeper 순찰에 남겨 acceptTimeout 만료 후 settleTimeout 으로 환불하게 한다.
     * 배정 전(assignedWorkerId IS NULL)일 때만 — 그 사이 매칭이 배정했으면 0행 → ASSIGNED 취소 경로가 잇는다.
     */
    @Modifying(clearAutomatically = true)
    @Query("""
            UPDATE Job j
               SET j.status = com.gshared.back.job.domain.JobStatus.CANCELLING,
                   j.cancelReason = 'USER_REQUEST',
                   j.reconciliationReason = :reconciliationReason
             WHERE j.id = :jobId
               AND j.owner.id = :ownerId
               AND j.assignedWorkerId IS NULL
               AND j.status = com.gshared.back.job.domain.JobStatus.FUNDED
            """)
    int cancelToRefundWaitIfFunded(@Param("jobId") Long jobId, @Param("ownerId") Long ownerId,
                                   @Param("reconciliationReason") String reconciliationReason);

    /**
     * 원자적 배정(매칭): "아직 FUNDED 일 때만" ASSIGNED 전이.
     * 매칭이 Job 을 읽은 뒤 사용자가 취소해도, 조건 불일치(0행)로 취소를 되살리지 않는다.
     * 트랜잭션 경계는 서비스 계층(JobService.assignJob)에서 관리.
     */
    @Modifying(clearAutomatically = true)
    @Query("""
            UPDATE Job j
               SET j.status = com.gshared.back.job.domain.JobStatus.ASSIGNED,
                   j.assignedWorkerId = :workerId
             WHERE j.id = :jobId
               AND j.status = com.gshared.back.job.domain.JobStatus.FUNDED
            """)
    int assignIfFunded(@Param("jobId") Long jobId, @Param("workerId") String workerId);

    /**
     * 배정 되돌리기: JOB_ASSIGN 전송 실패(죽은 워커 등)·일시적 거절(RPC 장애) 시 FUNDED 로 복귀 → 다음 순찰에 재매칭.
     * "그 워커에게 ASSIGNED 인 상태일 때만" — 이미 JOB_ACCEPTED 로 RUNNING 이 됐으면 건드리지 않는다.
     */
    @Modifying(clearAutomatically = true)
    @Query("""
            UPDATE Job j
               SET j.status = com.gshared.back.job.domain.JobStatus.FUNDED,
                   j.assignedWorkerId = null
             WHERE j.id = :jobId
               AND j.assignedWorkerId = :workerId
               AND j.status = com.gshared.back.job.domain.JobStatus.ASSIGNED
            """)
    int unassignIfAssigned(@Param("jobId") Long jobId, @Param("workerId") String workerId);

    /**
     * 원자적 예치 확인: "아직 CREATED 일 때만" FUNDED 전이 + 온체인 위치(chain·contract·jobId)·txHash 기록.
     * 호출 전에 체인 대조(JobFunded 이벤트·현재 상태)가 끝나 있어야 한다 — 여기는 기록만 한다.
     * 중복 호출·동시 호출은 조건 불일치(0행)로 한쪽만 성공한다.
     *
     * <p>ownerId 조건이 없는 이유: 예치 확인 API 는 소유 확인을 먼저 하고, reconciliation 은 주인 없이 돈다.
     * @return 영향받은 행 수 (1=전이 성공, 0=상태가 CREATED가 아님)
     */
    @Modifying(clearAutomatically = true)
    @Query("""
            UPDATE Job j
               SET j.status = com.gshared.back.job.domain.JobStatus.FUNDED,
                   j.escrowChainId = :chainId,
                   j.escrowContractAddress = :contractAddress,
                   j.escrowJobId = :escrowJobId,
                   j.fundTxHash = :fundTxHash
             WHERE j.id = :jobId
               AND j.status = com.gshared.back.job.domain.JobStatus.CREATED
            """)
    int fundIfCreated(@Param("jobId") Long jobId,
                      @Param("chainId") Long chainId, @Param("contractAddress") String contractAddress,
                      @Param("escrowJobId") String escrowJobId, @Param("fundTxHash") String fundTxHash);

    // ── keeper · reconciliation 순찰 ───────────────────────────

    /** 예치 확인 보류: txHash 는 받았는데 체인 확인을 못 한 Job. reconciliation 이 다시 확인한다. */
    @Query("""
            SELECT j FROM Job j
              JOIN FETCH j.owner
             WHERE j.status = com.gshared.back.job.domain.JobStatus.CREATED
               AND j.fundTxHash IS NOT NULL
             ORDER BY j.id ASC
            """)
    java.util.List<Job> findFundingPending();

    /**
     * 온체인에 살아 있는 Job — keeper 가 만료를 살피고, reconciliation 이 체인과 대조하는 대상.
     * CREATED(예치 전)·종료 상태는 온체인에 볼 것이 없거나 이미 끝났다.
     */
    @Query("""
            SELECT j FROM Job j
             WHERE j.escrowJobId IS NOT NULL
               AND j.status IN (com.gshared.back.job.domain.JobStatus.FUNDED,
                                com.gshared.back.job.domain.JobStatus.ASSIGNED,
                                com.gshared.back.job.domain.JobStatus.RUNNING,
                                com.gshared.back.job.domain.JobStatus.CANCELLING)
             ORDER BY j.id ASC
            """)
    java.util.List<Job> findOpenOnChain();

    /** 종료됐지만 환불 확인 기록이 없는 작업. 과거 FAILED와 수락 전 거절도 포함한다. */
    @Query("""
            SELECT j FROM Job j JOIN FETCH j.owner
             WHERE j.escrowJobId IS NOT NULL AND j.refundConfirmedAt IS NULL
               AND j.status IN (com.gshared.back.job.domain.JobStatus.FAILED,
                                com.gshared.back.job.domain.JobStatus.CANCELLED)
             ORDER BY j.id ASC
            """)
    java.util.List<Job> findRefundRecoveryCandidates();

    /** 예치 정보와 미정산 상태를 확인한 종료 작업만 환불 순찰에 되돌린다. 재실행/재매칭은 하지 않는다. */
    @Modifying(clearAutomatically = true)
    @Query("""
            UPDATE Job j
               SET j.status = com.gshared.back.job.domain.JobStatus.CANCELLING,
                   j.finishedAt = NULL,
                   j.reconciliationReason = :reason
             WHERE j.id = :jobId AND j.refundConfirmedAt IS NULL
               AND j.status IN (com.gshared.back.job.domain.JobStatus.FAILED,
                                com.gshared.back.job.domain.JobStatus.CANCELLED)
            """)
    int reopenForRefundIfTerminal(@Param("jobId") Long jobId, @Param("reason") String reason);

    /** 실제 체인 CANCELLED를 확인한 종료 작업은 복구 순찰에서 제외한다. */
    @Modifying(clearAutomatically = true)
    @Query("""
            UPDATE Job j SET j.refundConfirmedAt = :now, j.reconciliationReason = NULL
             WHERE j.id = :jobId AND j.refundConfirmedAt IS NULL
               AND j.status IN (com.gshared.back.job.domain.JobStatus.FAILED,
                                com.gshared.back.job.domain.JobStatus.CANCELLED)
            """)
    int confirmRefundIfTerminal(@Param("jobId") Long jobId, @Param("now") LocalDateTime now);

    /** 대조 필요 표시가 남은 Job. 자동 진행은 멈춰 있고 reconciliation 이 체인을 다시 본다. */
    @Query("""
            SELECT j FROM Job j
             WHERE j.reconciliationReason IS NOT NULL
               AND j.escrowJobId IS NOT NULL
             ORDER BY j.id ASC
            """)
    java.util.List<Job> findFlaggedForReconciliation();

    /**
     * 온체인 만료 정리(settleTimeout) 확인 후: 아직 살아 있는 상태면 CANCELLED 로 확정.
     * 예치금은 체인이 이미 돌려줬다 — 이 UPDATE 는 그 사실을 DB 에 옮길 뿐이다.
     * 워커 손에 있던 Job(ASSIGNED·RUNNING·CANCELLING)도 포함한다: 체인이 CANCELLED 면 더 진행할 수 없다.
     */
    @Modifying(clearAutomatically = true)
    @Query("""
            UPDATE Job j
               SET j.status = com.gshared.back.job.domain.JobStatus.CANCELLED,
                   j.abortTxHash = COALESCE(:txHash, j.abortTxHash),
                   j.cancelReason = COALESCE(j.cancelReason, :cancelReason),
                   j.refundConfirmedAt = :now,
                   j.finishedAt = :now
             WHERE j.id = :jobId
               AND j.status IN (com.gshared.back.job.domain.JobStatus.FUNDED,
                                com.gshared.back.job.domain.JobStatus.ASSIGNED,
                                com.gshared.back.job.domain.JobStatus.RUNNING,
                                com.gshared.back.job.domain.JobStatus.CANCELLING)
            """)
    // cancelReason 은 COALESCE — 사용자가 먼저 취소를 걸어(USER_REQUEST) 키퍼가 마무리한 경우,
    // 원인 제공자는 여전히 사용자다. 비어 있을 때만 호출자(키퍼=PROVIDER_TIMEOUT/대조기=SYSTEM_RECONCILED)가 채운다.
    int cancelByChainIfOpen(@Param("jobId") Long jobId, @Param("txHash") String txHash,
                            @Param("cancelReason") String cancelReason, @Param("now") LocalDateTime now);
}
