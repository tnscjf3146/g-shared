package com.gshared.back.global.blockchain;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.util.Optional;

/**
 * "체인은 뭐라고 하는가"에 대한 세 가지 답.
 *
 * <p>Backend 가 워커의 말을 받을 때마다 물어야 하는 질문이고, 답은 <b>둘이 아니라
 * 셋</b>이다. 맞다 · 틀리다 · 모르겠다. 셋째를 없애고 boolean 으로 접으면 RPC 장애가
 * "검증 실패"가 되어 멀쩡한 Job 이 죽거나, 반대로 "검증 통과"가 되어 확인한 적 없는
 * 것을 확인했다고 기록하게 된다.
 *
 * <p>어느 쪽으로 접든 다음 날 체인을 보면 거짓이었음이 드러난다. 그래서 모르는
 * 것은 모른다고 돌려주고, 무엇을 할지는 호출부가 그 상태에 맞게 정한다.
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class EscrowVerification {

    private final EscrowChainClient chain;

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

        /**
         * 확인됐거나, 확인할 수 없었는가.
         *
         * <p>진행을 막지 <b>않는</b> 조건이다. 체인이 명시적으로 아니라고 한 것만
         * 막는다 -- 아직 배포도 안 된 컨트랙트 때문에 모든 Job 이 멈추면 안 된다.
         */
        public boolean allowsProgress() {
            return this != CONTRADICTED;
        }
    }

    public boolean isConfigured() {
        return chain.isConfigured();
    }

    /**
     * JOB_ACCEPTED: 체인에서 이 Job 이 정말 이 operator 에게 ASSIGNED 인가.
     *
     * <p>워커는 "내가 온체인에서 수락했다"고 말하면서 txHash 를 준다. 그 말이 맞는지는
     * 트랜잭션 영수증이 아니라 <b>지금 상태</b>로 확인한다 -- 영수증은 되돌려진
     * 트랜잭션에도 붙고, 우리가 알고 싶은 것은 "그래서 지금 이 Job 은 누구 것인가"다.
     */
    public Verdict acceptedOnChain(String escrowJobId, String operatorAddress) {
        Optional<EscrowChainClient.OnChainJob> found = chain.readJob(escrowJobId);
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
    public Verdict resultOnChain(String escrowJobId, String operatorAddress,
                                 String expectedResultHash) {
        Optional<EscrowChainClient.OnChainJob> found = chain.readJob(escrowJobId);
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

    /** JOB_FAILED · JOB_CANCELLED: 예치금이 정말 돌아갔는가. */
    public Verdict cancelledOnChain(String escrowJobId) {
        Optional<EscrowChainClient.OnChainJob> found = chain.readJob(escrowJobId);
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
     * 체인이 이 예치에 붙인 jobId.
     *
     * <p>프런트가 준 값을 정본으로 쓰지 않기 위한 것이다. 비어 있으면 확인하지
     * 못한 것이고, 그때는 기존대로 받은 값을 쓴다 -- 확인 못 했다는 사실은 로그에
     * 남는다.
     */
    public Optional<String> escrowJobIdFor(String externalRef) {
        return chain.findFundedJob(externalRef).map(EscrowChainClient.FundedJob::escrowJobId);
    }
}
