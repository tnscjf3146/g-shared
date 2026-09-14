package com.gshared.back.job.service;

import com.gshared.back.global.blockchain.EscrowChainClient;
import com.gshared.back.global.blockchain.EscrowKeeperSigner;
import com.gshared.back.global.blockchain.EscrowVerification;
import com.gshared.back.job.domain.Job;
import com.gshared.back.job.repository.JobRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.time.Instant;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 시한이 지난 온체인 Job 을 {@code settleTimeout} 으로 정리하는 keeper (작업 정리 §7).
 *
 * <p>대상은 온체인이 FUNDED 인데 accept 시한이 지났거나, ASSIGNED 인데 execution 시한이 지난 Job 이다.
 * 둘 다 컨트랙트가 의뢰자에게 환불한다. 판단은 서버 시계가 아니라 <b>최신 블록의 timestamp</b> 로 한다 --
 * 컨트랙트가 그 시계로 되돌리기 때문이다.
 *
 * <p>순서: 전송 직전에 온체인 상태를 다시 읽는다(그 사이 수락·완료됐을 수 있다) → 서명·전송 → 영수증 성공 확인 →
 * 그제야 DB 를 CANCELLED 로 옮긴다. 영수증을 못 봤으면 DB 는 그대로 두고 다음 순찰이 상태를 다시 읽는다 --
 * 그때 CANCELLED 면 {@link EscrowReconciler} 가 옮긴다. 실패는 Job 별 지수 백오프(1분 → 최대 1시간)로 미룬다.
 *
 * <p>서버가 여럿이어도 안전하다: 한쪽이 먼저 정리하면 다른 쪽은 전송 직전 재조회에서 CANCELLED 를 보고 그만두고,
 * 정말 동시에 보내면 뒤의 트랜잭션은 컨트랙트가 되돌린다("not settleable"). DB 전이는 조건부 UPDATE 다.
 *
 * <p>keeper 키({@code escrow.keeper-private-key})가 없으면 돌지 않는다. SSAFY 체인은 가스비 0 이라 잔액은 필요 없다.
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class EscrowTimeoutKeeper {

    private static final Duration BACKOFF_BASE = Duration.ofMinutes(1);
    private static final Duration BACKOFF_MAX = Duration.ofHours(1);
    private static final int RECEIPT_POLLS = 10;             // 영수증 대기: 10 × 3초
    private static final long RECEIPT_POLL_MS = 3_000;

    private final JobRepository jobRepository;
    private final EscrowVerification escrow;
    private final EscrowChainClient chain;
    private final EscrowKeeperSigner signer;
    private final EscrowSettlementService settlement;

    /** Job 별 실패 횟수·다음 시도 시각. 프로세스가 죽으면 사라지지만, 그러면 다음 기동이 곧바로 다시 시도할 뿐이다. */
    private final Map<Long, Backoff> backoffs = new ConcurrentHashMap<>();

    @Scheduled(fixedDelay = 60_000, initialDelay = 45_000)
    public void sweep() {
        if (!signer.isEnabled()) {
            return;
        }
        Optional<Long> chainNow = chain.latestBlockTimestamp();
        if (chainNow.isEmpty()) {
            log.warn("[KEEPER] 최신 블록을 읽지 못했다 -- 이번 순찰은 건너뛴다");
            return;
        }
        Instant wallNow = Instant.now();
        for (Job job : jobRepository.findOpenOnChain()) {
            Backoff backoff = backoffs.get(job.getId());
            if (backoff != null && wallNow.isBefore(backoff.nextTryAt())) {
                continue;
            }
            try {
                settleIfExpired(job, chainNow.get());
            } catch (Exception e) {
                log.error("[KEEPER] jobId={} 정리 실패 (다음 Job 계속)", job.getId(), e);
                delay(job.getId());
            }
        }
    }

    private void settleIfExpired(Job job, long chainNow) {
        Long jobId = job.getId();
        Optional<EscrowChainClient.OnChainJob> read =
                escrow.stateOnChain(job.getEscrowContractAddress(), job.getEscrowJobId());
        if (read.isEmpty()) {
            return;   // 모른다. 백오프 없이 다음 순찰에.
        }
        EscrowChainClient.OnChainJob onChain = read.get();
        switch (onChain.state()) {
            case COMPLETED, CANCELLED -> {
                backoffs.remove(jobId);   // 이미 끝났다. DB 는 reconciler 가 맞춘다.
                return;
            }
            default -> { }
        }
        if (!onChain.isExpiredAt(chainNow)) {
            return;
        }

        long chainId = job.getEscrowChainId() == null ? 0L : job.getEscrowChainId();
        if (chainId == 0L) {
            log.warn("[KEEPER] jobId={} 에 escrowChainId 가 없다 -- 정리하지 않는다", jobId);
            return;
        }
        String contract = job.getEscrowContractAddress() != null
                ? job.getEscrowContractAddress() : chain.contractAddress();
        log.info("[KEEPER] 시한 만료 jobId={} escrowJobId={} state={} deadline={} chainNow={}",
                jobId, job.getEscrowJobId(), onChain.state(), onChain.deadline(), chainNow);

        Optional<String> txHash = signer.settleTimeout(chainId, contract, job.getEscrowJobId());
        if (txHash.isEmpty()) {
            delay(jobId);
            return;
        }
        if (!receiptSucceeded(txHash.get())) {
            // 되돌려졌거나 아직 안 실렸다. 되돌려진 이유는 대개 "그 사이 누가 먼저 정리함"이고,
            // 어느 쪽이든 다음 순찰의 재조회가 답한다.
            log.warn("[KEEPER] settleTimeout 영수증 미확인 jobId={} tx={}", jobId, txHash.get());
            delay(jobId);
            return;
        }
        backoffs.remove(jobId);
        settlement.cancelFromChain(jobId, txHash.get(),
                "ESCROW_TIMEOUT: settleTimeout from " + onChain.state() + " (deadline=" + onChain.deadline() + ")");
    }

    private boolean receiptSucceeded(String txHash) {
        for (int i = 0; i < RECEIPT_POLLS; i++) {
            EscrowChainClient.ReceiptLookup lookup = chain.readReceipt(txHash);
            if (lookup.status() == EscrowChainClient.ReceiptLookup.Status.FOUND) {
                return lookup.receipt().success();
            }
            try {
                Thread.sleep(RECEIPT_POLL_MS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return false;
            }
        }
        return false;
    }

    private void delay(Long jobId) {
        Backoff previous = backoffs.get(jobId);
        int attempts = previous == null ? 1 : previous.attempts() + 1;
        long factor = 1L << Math.min(attempts - 1, 10);
        Duration wait = BACKOFF_BASE.multipliedBy(factor);
        if (wait.compareTo(BACKOFF_MAX) > 0) {
            wait = BACKOFF_MAX;
        }
        backoffs.put(jobId, new Backoff(attempts, Instant.now().plus(wait)));
        log.info("[KEEPER] jobId={} 재시도 {}회째 예약 (+{})", jobId, attempts, wait);
    }

    private record Backoff(int attempts, Instant nextTryAt) {
    }
}
