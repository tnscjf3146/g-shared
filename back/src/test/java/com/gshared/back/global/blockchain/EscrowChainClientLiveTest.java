package com.gshared.back.global.blockchain;

import com.gshared.back.global.blockchain.EscrowVerification.Verdict;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 진짜 노드에 붙여 RPC 창구를 확인한다.
 *
 * <p>목으로는 인코딩이 틀려도 드러나지 않는다. {@code eth_call} 의 반환을 잘못 풀거나
 * {@code JobFunded} 의 topic0 를 잘못 계산해도, 스스로 만든 응답을 스스로 읽으면
 * 언제나 맞는다. 그래서 여기서는 실제로 배포된 컨트랙트의 실제 상태를 읽는다.
 *
 * <p>고정물은 blockchain 쪽에서 만든다:
 *
 * <pre>
 *   npx hardhat node
 *   npm run dev:chain
 *   npx hardhat run scripts/dev/live-fixture.ts --network localhost
 * </pre>
 *
 * <p>그러면 FUNDED · ASSIGNED · COMPLETED · CANCELLED 네 Job 이 체인에 남고,
 * 아래 환경 변수를 주고 이 테스트를 돌린다. <b>변수가 없으면 통째로 건너뛴다</b> --
 * 노드가 없는 CI 에서 붙지 못한 것을 실패로 적으면, 다음부터 아무도 그 빨간불을
 * 보지 않게 된다.
 *
 * <pre>
 *   ESCROW_RPC_URL=http://127.0.0.1:8545 \
 *   ESCROW_CONTRACT_ADDRESS=0x... \
 *   ESCROW_LIVE_OPERATOR=0x... \
 *   ESCROW_LIVE_PROVIDER=0x... \
 *   ESCROW_LIVE_RESULT_HASH=0x... \
 *   ESCROW_LIVE_FUNDED_REF=0x... \
 *   ./gradlew test --tests '*EscrowChainClientLiveTest'
 * </pre>
 */
@EnabledIfEnvironmentVariable(named = "ESCROW_RPC_URL", matches = ".+")
@EnabledIfEnvironmentVariable(named = "ESCROW_CONTRACT_ADDRESS", matches = ".+")
class EscrowChainClientLiveTest {

    private static EscrowChainClient chain;
    private static EscrowVerification escrow;

    private static String operator;
    private static String provider;
    private static String resultHash;
    private static String fundedRef;

    /** 고정물이 만든 순서 그대로. live-fixture.ts 가 1..4 로 찍는다. */
    private static final String FUNDED = "1";
    private static final String ASSIGNED = "2";
    private static final String COMPLETED = "3";
    private static final String CANCELLED = "4";

    @BeforeAll
    static void connect() {
        EscrowProperties properties = new EscrowProperties(
                System.getenv("ESCROW_CONTRACT_ADDRESS"), System.getenv("ESCROW_RPC_URL"), null, false, null);
        chain = new EscrowChainClient(properties);
        escrow = new EscrowVerification(chain, properties);
        operator = System.getenv("ESCROW_LIVE_OPERATOR");
        provider = System.getenv("ESCROW_LIVE_PROVIDER");
        resultHash = System.getenv("ESCROW_LIVE_RESULT_HASH");
        fundedRef = System.getenv("ESCROW_LIVE_FUNDED_REF");
    }

    @Test
    @DisplayName("jobs() 네 상태를 실제 노드에서 그대로 읽는다")
    void readsEveryState() {
        assertThat(chain.isConfigured()).isTrue();
        assertThat(chain.readJob(FUNDED)).get()
                .extracting(EscrowChainClient.OnChainJob::state).isEqualTo(EscrowJobState.FUNDED);
        assertThat(chain.readJob(ASSIGNED)).get()
                .extracting(EscrowChainClient.OnChainJob::state).isEqualTo(EscrowJobState.ASSIGNED);
        assertThat(chain.readJob(COMPLETED)).get()
                .extracting(EscrowChainClient.OnChainJob::state).isEqualTo(EscrowJobState.COMPLETED);
        assertThat(chain.readJob(CANCELLED)).get()
                .extracting(EscrowChainClient.OnChainJob::state).isEqualTo(EscrowJobState.CANCELLED);
    }

    @Test
    @DisplayName("ASSIGNED 한 줄의 나머지 칸도 자리를 맞춰 읽는다")
    void decodesEveryField() {
        EscrowChainClient.OnChainJob job = chain.readJob(ASSIGNED).orElseThrow();
        // 일곱 값의 순서가 어긋나면 상태는 맞아도 주소·금액이 서로 바뀐다.
        assertThat(job.operatorIs(operator)).isTrue();
        assertThat(job.provider()).isEqualToIgnoringCase(provider);
        assertThat(job.priceWei()).isEqualTo(new java.math.BigInteger("10000000000000000000"));
        assertThat(job.deadline()).isGreaterThan(0L);
    }

    @Test
    @DisplayName("COMPLETED 의 resultHash 가 제출한 값 그대로다")
    void readsResultHash() {
        assertThat(chain.readJob(COMPLETED).orElseThrow().resultHashIs(resultHash)).isTrue();
    }

    @Test
    @DisplayName("없는 Job 은 NONE 으로 답한다 — 못 읽은 것과 다르다")
    void missingJobIsNone() {
        assertThat(chain.readJob("999999")).get()
                .extracting(EscrowChainClient.OnChainJob::state).isEqualTo(EscrowJobState.NONE);
    }

    @Test
    @DisplayName("isOperator 가 승인 여부를 그대로 답한다")
    void readsOperatorApproval() {
        assertThat(chain.isOperator(provider, operator)).contains(true);
        // 승인받은 적 없는 주소. 거짓이지 '모름'이 아니다.
        assertThat(chain.isOperator(provider, "0x000000000000000000000000000000000000dEaD"))
                .contains(false);
    }

    @Test
    @DisplayName("JobFunded 로그에서 체인이 붙인 jobId 를 찾는다")
    void findsFundedJobByExternalRef() {
        Optional<EscrowChainClient.JobFundedEvent> found = chain.findFundedJob(fundedRef);
        assertThat(found).get().extracting(EscrowChainClient.JobFundedEvent::escrowJobId)
                .isEqualTo(FUNDED);
        assertThat(found.orElseThrow().provider()).isEqualToIgnoringCase(provider);
        assertThat(escrow.escrowJobIdFor(fundedRef)).contains(FUNDED);
    }

    @Test
    @DisplayName("모르는 externalRef 는 빈 값 — 아무 Job 이나 골라 주지 않는다")
    void unknownRefFindsNothing() {
        assertThat(chain.findFundedJob(
                "0x1111111111111111111111111111111111111111111111111111111111111111"))
                .isEmpty();
    }

    @Test
    @DisplayName("세 판정이 실제 체인 상태와 맞아떨어진다")
    void verdictsMatchTheChain() {
        // 수락: ASSIGNED + 그 operator 여야 CONFIRMED
        assertThat(escrow.acceptedOnChain(null, ASSIGNED, operator)).isEqualTo(Verdict.CONFIRMED);
        assertThat(escrow.acceptedOnChain(null, FUNDED, operator)).isEqualTo(Verdict.CONTRADICTED);
        assertThat(escrow.acceptedOnChain(null, ASSIGNED,
                "0x000000000000000000000000000000000000dEaD")).isEqualTo(Verdict.CONTRADICTED);

        // 결과: COMPLETED + 해시 일치
        assertThat(escrow.resultOnChain(null, COMPLETED, operator, resultHash)).isEqualTo(Verdict.CONFIRMED);
        assertThat(escrow.resultOnChain(null, COMPLETED, operator,
                "0x2222222222222222222222222222222222222222222222222222222222222222"))
                .isEqualTo(Verdict.CONTRADICTED);
        assertThat(escrow.resultOnChain(null, ASSIGNED, operator, resultHash)).isEqualTo(Verdict.CONTRADICTED);

        // 취소: CANCELLED 여야 CONFIRMED
        assertThat(escrow.cancelledOnChain(null, CANCELLED)).isEqualTo(Verdict.CONFIRMED);
        assertThat(escrow.cancelledOnChain(null, ASSIGNED)).isEqualTo(Verdict.CONTRADICTED);

        // 승인
        assertThat(escrow.operatorApproved(provider, operator)).isEqualTo(Verdict.CONFIRMED);
        assertThat(escrow.operatorApproved(provider,
                "0x000000000000000000000000000000000000dEaD")).isEqualTo(Verdict.CONTRADICTED);
    }

    @Test
    @DisplayName("노드가 없으면 UNKNOWN — CONTRADICTED 로 접지 않는다")
    void unreachableNodeIsUnknown() {
        // 닫힌 포트. '틀렸다'가 아니라 '못 물어봤다'여야 한다.
        EscrowProperties blindProperties = new EscrowProperties(
                System.getenv("ESCROW_CONTRACT_ADDRESS"), "http://127.0.0.1:9", null, false, null);
        EscrowVerification blind = new EscrowVerification(new EscrowChainClient(blindProperties), blindProperties);
        assertThat(blind.acceptedOnChain(null, ASSIGNED, operator)).isEqualTo(Verdict.UNKNOWN);
        assertThat(blind.cancelledOnChain(null, CANCELLED)).isEqualTo(Verdict.UNKNOWN);
        assertThat(blind.operatorApproved(provider, operator)).isEqualTo(Verdict.UNKNOWN);
        assertThat(blind.escrowJobIdFor(fundedRef)).isEmpty();
    }
}
