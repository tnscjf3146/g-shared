package com.gshared.back.job.service;

import com.gshared.back.global.blockchain.EscrowChainClient;
import com.gshared.back.job.domain.Job;
import com.gshared.back.job.domain.JobStatus;
import com.gshared.back.job.repository.JobRepository;
import com.gshared.back.worker.repository.WorkerRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;

/**
 * 체인이 말하는 것을 DB 에 옮기는 전이들. keeper 와 reconciliation 이 쓴다.
 *
 * <p>워커의 보고를 받는 {@link JobService} 와 다른 점: 여기서는 <b>체인이 이미 확인된 뒤</b>다.
 * 호출부가 온체인 상태를 읽고 "체인은 CANCELLED 다 / COMPLETED 다 / 이 operator 에게 ASSIGNED 다"를
 * 확인한 다음 부른다. 그래서 검증 없이 조건부 UPDATE 만 한다 -- 조건이 어긋나면(그 사이 다른 경로가
 * 먼저 확정) 0행이고, 그것으로 끝이다.
 *
 * <p>Job 하나가 한 트랜잭션이다. 순찰 전체를 한 트랜잭션에 넣으면 Job 하나의 실패가 나머지를 되돌린다.
 */
@Slf4j
@Service
@RequiredArgsConstructor
@Transactional
public class EscrowSettlementService {

    private final JobRepository jobRepository;
    private final WorkerRepository workerRepository;
    private final JobService jobService;

    /** 호출자가 예치의 동일성과 미정산 상태를 체인에서 확인한 뒤 사용한다. */
    public boolean recoverRefund(Long jobId) {
        String reason = "REFUND_RECOVERY: deposit still locked; awaiting settleTimeout";
        // FAILED/CANCELLED 는 되돌리는 규칙이 달라(FAILED 만 TRAINING_FAILED 선기록) 쿼리를 나눴다 — 상태당 하나만 맞는다
        int updated = jobRepository.reopenFailedForRefund(jobId, reason);
        if (updated == 0) {
            updated = jobRepository.reopenCancelledForRefund(jobId, reason);
        }
        if (updated > 0) {
            log.warn("[REFUND] 미환불 종료 작업을 CANCELLING으로 복구 jobId={}", jobId);
        }
        return updated > 0;
    }

    /** 이미 환불된 종료 작업은 상태와 원래 실패 사유를 보존한다. */
    public void confirmTerminalRefund(Long jobId) {
        jobRepository.confirmRefundIfTerminal(jobId, LocalDateTime.now());
    }

    /** reconciliation 이 체인에서 확인한 예치. {@link JobRepository#fundIfCreated} 그대로. */
    public boolean fundFromChain(Long jobId, Long chainId, String contractAddress,
                                 EscrowChainClient.JobFundedEvent event, String txHash) {
        int updated = jobRepository.fundIfCreated(jobId, chainId, contractAddress, event.escrowJobId(), txHash);
        if (updated > 0) {
            log.info("[RECONCILE] 보류 예치 확정 jobId={} escrowJobId={} tx={}", jobId, event.escrowJobId(), txHash);
            jobRepository.findById(jobId).ifPresent(Job::clearReconciliation);
        }
        return updated > 0;
    }

    /** 보류 예치가 체인 대조에서 거부됐다. txHash 를 비우고 사유를 남긴다. */
    public void rejectFunding(Long jobId, String reason) {
        jobRepository.findById(jobId).ifPresent(job -> {
            log.warn("[RECONCILE] 보류 예치 거부 jobId={} {}", jobId, reason);
            job.rejectFunding(reason);
        });
    }

    /**
     * 체인이 CANCELLED 다(settleTimeout · cancelJob · abortJob 중 무엇이든 예치금은 돌아갔다).
     * 살아 있는 상태였다면 CANCELLED 로 확정하고, 워커 손에 있었다면 놓아주고 JOB_CANCEL 을 보낸다 --
     * 정산될 돈이 없으니 컨테이너를 더 돌릴 이유가 없다.
     *
     * @param cancelReasonCode 원인 제공자 코드 — 키퍼 시한 환불·deadline 경과 추정은 PROVIDER_TIMEOUT,
     *                         그 외 대조기 발견은 SYSTEM_RECONCILED. 사용자가 먼저 취소했다면(USER_REQUEST)
     *                         cancelByChainIfOpen 의 COALESCE 가 그것을 지킨다.
     */
    public boolean cancelFromChain(Long jobId, String txHash, String reason, String cancelReasonCode) {
        Job before = jobRepository.findById(jobId).orElse(null);
        if (before == null) {
            return false;
        }
        String workerId = before.getAssignedWorkerId();
        boolean wasWithWorker = before.getStatus() == JobStatus.ASSIGNED
                || before.getStatus() == JobStatus.RUNNING
                || before.getStatus() == JobStatus.CANCELLING;

        int updated = jobRepository.cancelByChainIfOpen(jobId, txHash, cancelReasonCode, LocalDateTime.now());
        if (updated == 0) {
            return false;
        }
        log.info("[RECONCILE] 체인 CANCELLED → DB CANCELLED jobId={} from={} tx={} reason={}",
                jobId, before.getStatus(), txHash, reason);
        jobRepository.findById(jobId).ifPresent(Job::clearReconciliation);
        if (wasWithWorker && workerId != null) {
            workerRepository.findById(workerId)
                    .filter(worker -> jobId.equals(worker.getCurrentJobId()))
                    .ifPresent(worker -> worker.finishJob());
            jobService.sendCancel(workerId, jobId, "ESCROW_CANCELLED");
        }
        return true;
    }

    /**
     * 체인이 이 워커의 operator 에게 ASSIGNED 인데 DB 는 아직 ASSIGNED 다 -- JOB_ACCEPTED 를 놓쳤거나
     * 운영 정책으로 보류했던 것. RUNNING 으로 올린다. acceptTxHash 는 모른다(이벤트 로그를 뒤지면 알 수 있지만
     * 상태 확정에 필요하지 않다).
     */
    public boolean startFromChain(Long jobId, String workerId) {
        int updated = jobRepository.startIfAssigned(jobId, workerId, null, LocalDateTime.now());
        if (updated == 0) {
            return false;
        }
        log.info("[RECONCILE] 체인 ASSIGNED → DB RUNNING jobId={} workerId={}", jobId, workerId);
        jobRepository.findById(jobId).ifPresent(Job::clearReconciliation);
        workerRepository.findById(workerId).ifPresent(worker -> worker.startJob(jobId));
        return true;
    }

    /**
     * 체인이 COMPLETED 이고 resultHash 가 §18 에서 Backend 가 검증한 업로드와 같다 -- JOB_RESULT 를 놓쳤거나
     * 보류했던 것. COMPLETED 로 확정한다. submitTxHash·실측 시간은 모른다.
     */
    public boolean completeFromChain(Long jobId, String workerId) {
        Job job = jobRepository.findById(jobId).orElse(null);
        if (job == null || job.getResultSha256() == null) {
            return false;
        }
        String resultHash = "0x" + job.getResultSha256();
        LocalDateTime now = LocalDateTime.now();
        int updated = job.getStatus() == JobStatus.RUNNING
                ? jobRepository.completeIfRunning(jobId, workerId,
                        job.getResultObjectKey(), resultHash, null, null, now)
                : jobRepository.completeIfCancelling(jobId, workerId,
                        job.getResultObjectKey(), resultHash, null, null,
                        "체인이 COMPLETED 라 취소가 아니라 완료로 확정했다 (reconciliation).", now);
        if (updated == 0) {
            return false;
        }
        log.info("[RECONCILE] 체인 COMPLETED → DB COMPLETED jobId={} workerId={}", jobId, workerId);
        jobRepository.findById(jobId).ifPresent(Job::clearReconciliation);
        workerRepository.findById(workerId)
                .filter(worker -> jobId.equals(worker.getCurrentJobId()))
                .ifPresent(worker -> worker.finishJob());
        return true;
    }

    /** 체인과 DB 가 어긋나는데 자동으로 풀 수 없다 -- 표시만 남긴다. 이미 같은 사유면 건드리지 않는다. */
    public void flag(Long jobId, String reason) {
        jobRepository.findById(jobId)
                .filter(job -> !reason.equals(job.getReconciliationReason()))
                .ifPresent(job -> {
                    log.warn("[RECONCILE] 대조 필요 jobId={} {}", jobId, reason);
                    job.flagReconciliation(reason);
                });
    }

    /** 다시 읽어 보니 체인과 맞는다 -- 표시를 걷는다. */
    public void clearFlag(Long jobId) {
        jobRepository.findById(jobId)
                .filter(job -> job.getReconciliationReason() != null)
                .ifPresent(job -> {
                    log.info("[RECONCILE] 체인과 일치 확인 → 표시 해제 jobId={}", jobId);
                    job.clearReconciliation();
                });
    }
}
