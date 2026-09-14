package com.gshared.back.job.service;

import com.gshared.back.global.blockchain.EscrowChainClient;
import com.gshared.back.global.blockchain.EscrowJobState;
import com.gshared.back.global.blockchain.EscrowProperties;
import com.gshared.back.global.blockchain.EscrowVerification;
import com.gshared.back.global.blockchain.TokenUnits;
import com.gshared.back.global.wallet.WalletAuthProperties;
import com.gshared.back.job.domain.Job;
import com.gshared.back.job.domain.JobStatus;
import com.gshared.back.job.repository.JobRepository;
import com.gshared.back.worker.repository.WorkerRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Optional;

/**
 * 체인과 DB 를 다시 맞추는 순찰 (작업 정리 §8).
 *
 * <p>RPC 장애·프로세스 재시작·보류(fail closed) 뒤에 남는 것들을 다시 본다:
 * <ul>
 *   <li>예치 보류 -- CREATED 인데 fundTxHash 가 있는 Job. 영수증을 다시 읽어 확정하거나 거부한다.
 *   <li>온체인에 살아 있는 Job(FUNDED·ASSIGNED·RUNNING·CANCELLING) -- 체인 상태를 읽어, 체인이 먼저 끝났으면
 *       DB 를 따라가게 한다(CANCELLED · COMPLETED · ASSIGNED→RUNNING). 워커의 보고를 놓쳤거나 보류했던 것들이
 *       여기서 풀린다.
 *   <li>대조 표시가 남은 종료 Job -- 지금은 맞으면 표시를 걷는다.
 * </ul>
 *
 * <p>체인이 정본이다. DB 가 체인보다 앞서 있는 경우(체인 FUNDED 인데 DB RUNNING 등)는 여기서 손대지 않는다 --
 * 워커가 아직 acceptJob 을 안 보냈을 수 있고, 시한이 지나면 keeper 가 정리한다.
 * 자동으로 풀 수 없는 어긋남은 표시만 남기고 사람이 본다.
 *
 * <p>순찰은 트랜잭션 밖이고 Job 하나마다 {@link EscrowSettlementService} 가 짧은 트랜잭션을 연다.
 * 여러 서버가 동시에 돌아도 전이는 전부 조건부 UPDATE 라 한쪽만 성공한다.
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class EscrowReconciler {

    private final JobRepository jobRepository;
    private final WorkerRepository workerRepository;
    private final EscrowVerification escrow;
    private final EscrowProperties escrowProperties;
    private final WalletAuthProperties walletAuthProperties;
    private final EscrowSettlementService settlement;

    @Scheduled(fixedDelay = 120_000, initialDelay = 30_000)
    public void reconcile() {
        if (!escrow.isConfigured()) {
            return;
        }
        reconcilePendingFunding();
        reconcileOpenJobs();
        reconcileFlaggedTerminal();
    }

    // ── 예치 보류 ───────────────────────────────────────────────

    private void reconcilePendingFunding() {
        List<Job> pending = jobRepository.findFundingPending();
        for (Job job : pending) {
            try {
                EscrowVerification.FundingCheck check = escrow.fundingOnChain(
                        job.getFundTxHash(), job.getExternalRef(), job.getOwner().getWalletAddress(),
                        job.getProviderWallet(), TokenUnits.toWei(job.getEstimatedCost()));
                switch (check.verdict()) {
                    case CONFIRMED -> settlement.fundFromChain(job.getId(), walletAuthProperties.chainId(),
                            escrowProperties.contractAddressOrNull(), check.event(), job.getFundTxHash());
                    case CONTRADICTED -> settlement.rejectFunding(job.getId(), check.reason());
                    default -> log.debug("[RECONCILE] 예치 아직 확인 불가 jobId={} {}", job.getId(), check.reason());
                }
            } catch (Exception e) {
                log.error("[RECONCILE] 예치 보류 처리 실패 jobId={} (다음 Job 계속)", job.getId(), e);
            }
        }
    }

    // ── 온체인에 살아 있는 Job ──────────────────────────────────

    private void reconcileOpenJobs() {
        for (Job job : jobRepository.findOpenOnChain()) {
            try {
                reconcileOne(job);
            } catch (Exception e) {
                log.error("[RECONCILE] 대조 실패 jobId={} (다음 Job 계속)", job.getId(), e);
            }
        }
    }

    private void reconcileOne(Job job) {
        Optional<EscrowChainClient.OnChainJob> read =
                escrow.stateOnChain(job.getEscrowContractAddress(), job.getEscrowJobId());
        if (read.isEmpty()) {
            return;   // 모른다. 다음 순찰에.
        }
        EscrowChainClient.OnChainJob chain = read.get();
        Long jobId = job.getId();
        JobStatus db = job.getStatus();

        switch (chain.state()) {
            case CANCELLED -> settlement.cancelFromChain(jobId, null,
                    "ESCROW_CANCELLED: refunded on-chain (found by reconciliation, db was " + db + ")");

            case COMPLETED -> {
                if (db == JobStatus.RUNNING || db == JobStatus.CANCELLING) {
                    String verified = job.getResultSha256() == null ? null : "0x" + job.getResultSha256();
                    if (verified != null && chain.resultHashIs(verified)) {
                        settlement.completeFromChain(jobId, job.getAssignedWorkerId());
                    } else {
                        settlement.flag(jobId, "chain COMPLETED but no verified upload matches on-chain resultHash "
                                + chain.resultHash());
                    }
                } else {
                    // FUNDED·ASSIGNED 인데 체인이 COMPLETED — 워커가 수락도 결과도 보고하지 않았다.
                    settlement.flag(jobId, "chain COMPLETED while db is " + db);
                }
            }

            case ASSIGNED -> {
                if (db == JobStatus.ASSIGNED) {
                    String operator = operatorOf(job.getAssignedWorkerId());
                    if (operator != null && chain.operatorIs(operator)) {
                        settlement.startFromChain(jobId, job.getAssignedWorkerId());
                    } else {
                        settlement.flag(jobId, "chain ASSIGNED to " + chain.operator()
                                + " but db assigned worker operator is " + operator);
                    }
                } else if (db == JobStatus.FUNDED) {
                    // 배정을 되돌렸는데(JOB_ASSIGN 전송 실패 등) 워커는 온체인에서 수락했다.
                    settlement.flag(jobId, "chain ASSIGNED to " + chain.operator() + " but db is FUNDED");
                }
                // RUNNING·CANCELLING 이면 정상 — 결과·취소 보고를 기다린다.
            }

            case FUNDED -> {
                // DB 가 앞서 있다(ASSIGNED·RUNNING·CANCELLING). 워커가 acceptJob 을 아직 안 보냈거나 실패했다.
                // 시한이 지나면 keeper 가 settleTimeout 으로 정리하고, 그 결과가 다음 순찰에 CANCELLED 로 잡힌다.
                if (db == JobStatus.FUNDED) {
                    settlement.clearFlag(jobId);
                }
            }

            case NONE -> settlement.flag(jobId, "escrow job " + job.getEscrowJobId()
                    + " does not exist on " + job.getEscrowContractAddress());
        }
    }

    // ── 대조 표시가 남은 종료 Job ────────────────────────────────

    private void reconcileFlaggedTerminal() {
        for (Job job : jobRepository.findFlaggedForReconciliation()) {
            JobStatus db = job.getStatus();
            if (db != JobStatus.COMPLETED && db != JobStatus.CANCELLED && db != JobStatus.FAILED) {
                continue;   // 살아 있는 것은 위에서 봤다
            }
            try {
                escrow.stateOnChain(job.getEscrowContractAddress(), job.getEscrowJobId()).ifPresent(chain -> {
                    boolean agrees = (db == JobStatus.COMPLETED && chain.state() == EscrowJobState.COMPLETED)
                            || (db == JobStatus.CANCELLED && chain.state() == EscrowJobState.CANCELLED)
                            || (db == JobStatus.FAILED && chain.state() == EscrowJobState.CANCELLED);
                    if (agrees) {
                        settlement.clearFlag(job.getId());
                    }
                });
            } catch (Exception e) {
                log.error("[RECONCILE] 종료 Job 대조 실패 jobId={}", job.getId(), e);
            }
        }
    }

    private String operatorOf(String workerId) {
        if (workerId == null) {
            return null;
        }
        return workerRepository.findById(workerId).map(worker -> worker.getOperatorAddress()).orElse(null);
    }
}
