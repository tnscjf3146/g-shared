package com.gshared.back.global.blockchain;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.math.BigInteger;
import java.util.Optional;

/**
 * "체인은 뭐라고 하는가"에 대한 세 가지 답.
 *
 * <p>Backend 가 워커·프런트의 말을 받을 때마다 물어야 하는 질문이고, 답은 <b>둘이 아니라
 * 셋</b>이다. 맞다 · 틀리다 · 모르겠다. 셋째를 없애고 boolean 으로 접으면 RPC 장애가
 * "검증 실패"가 되어 멀쩡한 Job 이 죽거나, 반대로 "검증 통과"가 되어 확인한 적 없는
 * 것을 확인했다고 기록하게 된다.
 *
 * <p>모르는 것을 어떻게 할지는 <b>정책</b>이다({@link #blocks(Verdict)}). 운영은
 * 체인 확인 없이 아무것도 확정하지 않고(fail closed, {@code escrow.verification-required=true}),
 * 체인 없는 로컬은 모르는 것을 통과시킨다. 정책이 어느 쪽이든 CONTRADICTED 는 항상 막는다.
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class EscrowVerification {

    private final EscrowChainClient chain;
    private final EscrowProperties properties;

    /** 체인에 물어본 결과. */
    public enum Verdict {
        /** 체인이 그렇다고 답했다. */
        CONFIRMED,
        /** 체인이 아니라고 답했다. */
        CONTRADICTED,
        /** 물어보지 못했다 -- 설정이 없거나 노드가 답하지 않았다. */
        UNKNOWN;

        public boolean isContradicted() {
            return this == CONTRADICTED;
        }

        public boolean isConfirmed() {
            return this == CONFIRMED;
        }
    }

    public boolean isConfigured() {
        return chain.isConfigured();
    }

    /** 체인 확인 없이는 확정하지 않는 운영 정책인가. */
    public boolean isRequired() {
        return properties.verificationRequired();
    }

    /**
     * 이 답으로는 상태를 바꿀 수 없는가.
     *
     * <p>CONTRADICTED 는 언제나 막는다. UNKNOWN 은 운영({@code verification-required})에서만
     * 막는다 -- 확인하지 못한 것을 확정하면 RPC 장애가 검증 통과처럼 처리된다. 로컬에서는
     * 체인 없이도 흐름을 돌려 봐야 하므로 통과시킨다.
     */
    public boolean blocks(Verdict verdict) {
        return verdict == Verdict.CONTRADICTED
                || (verdict == Verdict.UNKNOWN && isRequired());
    }

    /**
     * 예치 확인: 프런트가 준 txHash 가 <b>이 Job 의</b> 예치인가.
     *
     * <p>영수증이 성공이고, 목적지가 우리 에스크로이고, 그 안의 JobFunded 가
     * externalRef·client·provider·price 를 전부 Backend 값과 맞추고, 지금 온체인 상태가
     * FUNDED 여야 CONFIRMED 다. 하나라도 어긋나면 CONTRADICTED 이고 사유가 붙는다.
     * 영수증이 아직 없거나(채굴 전) 노드가 답하지 않으면 UNKNOWN 이다.
     *
     * <p>escrowJobId 는 프런트가 아니라 <b>이벤트</b>에서 읽는다 -- 그것이 체인이 이 예치에
     * 붙인 번호다.
     */
    public FundingCheck fundingOnChain(String txHash, String expectedExternalRef,
                                       String expectedClient, String expectedProvider,
                                       BigInteger expectedPriceWei) {
        EscrowChainClient.ReceiptLookup lookup = chain.readReceipt(txHash);
        switch (lookup.status()) {
            case UNREACHABLE -> {
                return FundingCheck.unknown("receipt lookup failed");
            }
            case NOT_FOUND -> {
                return FundingCheck.unknown("transaction not yet mined");
            }
            default -> { }
        }
        EscrowChainClient.Receipt receipt = lookup.receipt();
        if (!receipt.success()) {
            return FundingCheck.contradicted("transaction reverted");
        }
        if (!receipt.sentTo(chain.contractAddress())) {
            return FundingCheck.contradicted("transaction was not sent to the escrow contract");
        }
        Optional<EscrowChainClient.JobFundedEvent> found = chain.jobFundedIn(receipt);
        if (found.isEmpty()) {
            return FundingCheck.contradicted("no JobFunded event from the escrow in this transaction");
        }
        EscrowChainClient.JobFundedEvent event = found.get();
        if (!event.externalRefIs(expectedExternalRef)) {
            return FundingCheck.contradicted("externalRef mismatch: event=" + event.externalRef());
        }
        if (!event.clientIs(expectedClient)) {
            return FundingCheck.contradicted("client mismatch: event=" + event.client());
        }
        if (!event.providerIs(expectedProvider)) {
            return FundingCheck.contradicted("provider mismatch: event=" + event.provider()
                    + " expected=" + expectedProvider);
        }
        if (expectedPriceWei == null || expectedPriceWei.compareTo(event.priceWei()) != 0) {
            return FundingCheck.contradicted("price mismatch: event=" + event.priceWei()
                    + " expected=" + expectedPriceWei);
        }

        // 이벤트는 "그때 예치됐다"이고, 지금 상태는 따로 본다 -- 그 사이 cancelJob 으로
        // 되찾아 갔을 수 있다. 되찾아 간 예치를 근거로 매칭을 돌리면 공급자는 공짜로 일한다.
        Optional<EscrowChainClient.OnChainJob> now = chain.readJob(event.escrowJobId());
        if (now.isEmpty()) {
            return FundingCheck.unknown("jobs() lookup failed after event matched");
        }
        if (now.get().state() != EscrowJobState.FUNDED) {
            return FundingCheck.contradicted("escrow job " + event.escrowJobId()
                    + " is " + now.get().state() + " (not FUNDED)");
        }
        return FundingCheck.confirmed(event);
    }

    /**
     * JOB_ACCEPTED: 체인에서 이 Job 이 정말 이 operator 에게 ASSIGNED 인가.
     *
     * <p>워커는 "내가 온체인에서 수락했다"고 말하면서 txHash 를 준다. 그 말이 맞는지는
     * 트랜잭션 영수증이 아니라 <b>지금 상태</b>로 확인한다 -- 영수증은 되돌려진
     * 트랜잭션에도 붙고, 우리가 알고 싶은 것은 "그래서 지금 이 Job 은 누구 것인가"다.
     */
    public Verdict acceptedOnChain(String contractAddress, String escrowJobId, String operatorAddress) {
        Optional<EscrowChainClient.OnChainJob> found = read(contractAddress, escrowJobId);
        if (found.isEmpty()) {
            return Verdict.UNKNOWN;
        }
        EscrowChainClient.OnChainJob job = found.get();
        if (job.state() != EscrowJobState.ASSIGNED) {
            log.warn("[CHAIN] escrowJob {} 은 {} 다 (ASSIGNED 가 아님)",
                    escrowJobId, job.state());
            return Verdict.CONTRADICTED;
        }
        if (!job.operatorIs(operatorAddress)) {
            log.warn("[CHAIN] escrowJob {} 의 operator 가 다르다", escrowJobId);
            return Verdict.CONTRADICTED;
        }
        return Verdict.CONFIRMED;
    }

    /**
     * JOB_RESULT: 체인이 COMPLETED 이고 기록된 resultHash 가 Backend 가 직접 계산한
     * 것과 같은가.
     *
     * <p>{@code expectedResultHash} 는 워커가 보낸 값이 아니라 §18 업로드 검증에서
     * Backend 가 오브젝트를 직접 해시한 값이어야 한다. 워커의 말을 워커의 말과
     * 대조하면 아무것도 검증되지 않는다.
     */
    public Verdict resultOnChain(String contractAddress, String escrowJobId, String operatorAddress,
                                 String expectedResultHash) {
        Optional<EscrowChainClient.OnChainJob> found = read(contractAddress, escrowJobId);
        if (found.isEmpty()) {
            return Verdict.UNKNOWN;
        }
        EscrowChainClient.OnChainJob job = found.get();
        if (job.state() != EscrowJobState.COMPLETED) {
            log.warn("[CHAIN] escrowJob {} 은 {} 다 (COMPLETED 가 아님)",
                    escrowJobId, job.state());
            return Verdict.CONTRADICTED;
        }
        if (!job.resultHashIs(expectedResultHash)) {
            log.error("[CHAIN] escrowJob {} 의 온체인 resultHash 가 검증된 해시와 다르다",
                    escrowJobId);
            return Verdict.CONTRADICTED;
        }
        if (operatorAddress != null && !job.operatorIs(operatorAddress)) {
            log.warn("[CHAIN] escrowJob {} 을 끝낸 operator 가 다르다", escrowJobId);
            return Verdict.CONTRADICTED;
        }
        return Verdict.CONFIRMED;
    }

    /** JOB_FAILED · JOB_CANCELLED · keeper: 예치금이 정말 돌아갔는가. */
    public Verdict cancelledOnChain(String contractAddress, String escrowJobId) {
        Optional<EscrowChainClient.OnChainJob> found = read(contractAddress, escrowJobId);
        if (found.isEmpty()) {
            return Verdict.UNKNOWN;
        }
        EscrowJobState state = found.get().state();
        if (state != EscrowJobState.CANCELLED) {
            log.warn("[CHAIN] escrowJob {} 은 {} 다 (CANCELLED 가 아님)", escrowJobId, state);
            return Verdict.CONTRADICTED;
        }
        return Verdict.CONFIRMED;
    }

    /** reconciliation: 지금 온체인 상태 그대로. 읽지 못했으면 비어 있다. */
    public Optional<EscrowChainClient.OnChainJob> stateOnChain(String contractAddress, String escrowJobId) {
        return read(contractAddress, escrowJobId);
    }

    /**
     * 매칭: 이 공급자가 이 operator 키를 승인했는가.
     *
     * <p>승인하지 않은 워커에게 Job 을 배정하면 {@code acceptJob} 이 온체인에서
     * 되돌려지고, 그 사이 고객의 예치금은 묶여 있다. 배정 전에 거르는 편이 싸다.
     *
     * <p>모를 때는 <b>거르지 않는다.</b> RPC 장애 동안 멀쩡한 공급자가 후보에서
     * 조용히 빠지면 Job 은 배정되지 않고 아무도 이유를 모른다.
     */
    public Verdict operatorApproved(String providerWallet, String operatorAddress) {
        Optional<Boolean> approved = chain.isOperator(providerWallet, operatorAddress);
        if (approved.isEmpty()) {
            return Verdict.UNKNOWN;
        }
        if (!approved.get()) {
            log.warn("[CHAIN] provider 가 operator 를 승인하지 않았다");
            return Verdict.CONTRADICTED;
        }
        return Verdict.CONFIRMED;
    }

    /**
     * 체인이 이 예치에 붙인 jobId. txHash 없이 externalRef 만 있을 때(reconciliation) 쓴다.
     */
    public Optional<String> escrowJobIdFor(String externalRef) {
        return chain.findFundedJob(externalRef).map(EscrowChainClient.JobFundedEvent::escrowJobId);
    }

    /** Job 에 기록된 주소가 있으면 그 컨트랙트를, 없으면(예전 데이터) 지금 설정된 것을 읽는다. */
    private Optional<EscrowChainClient.OnChainJob> read(String contractAddress, String escrowJobId) {
        String target = contractAddress == null || contractAddress.isBlank()
                ? chain.contractAddress() : contractAddress;
        return chain.readJob(target, escrowJobId);
    }

    /** 예치 확인의 답. CONFIRMED 면 이벤트가, 아니면 사유가 있다. */
    public record FundingCheck(Verdict verdict, EscrowChainClient.JobFundedEvent event, String reason) {

        static FundingCheck confirmed(EscrowChainClient.JobFundedEvent event) {
            return new FundingCheck(Verdict.CONFIRMED, event, null);
        }

        static FundingCheck contradicted(String reason) {
            return new FundingCheck(Verdict.CONTRADICTED, null, reason);
        }

        static FundingCheck unknown(String reason) {
            return new FundingCheck(Verdict.UNKNOWN, null, reason);
        }
    }
}
