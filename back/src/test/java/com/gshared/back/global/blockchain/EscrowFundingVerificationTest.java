package com.gshared.back.global.blockchain;

import com.gshared.back.global.blockchain.EscrowVerification.Verdict;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.web3j.abi.TypeEncoder;
import org.web3j.abi.datatypes.Address;
import org.web3j.abi.datatypes.generated.Bytes32;
import org.web3j.abi.datatypes.generated.Uint256;
import org.web3j.abi.datatypes.generated.Uint64;
import org.web3j.abi.datatypes.generated.Uint8;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.io.OutputStream;
import java.math.BigInteger;
import java.net.InetSocketAddress;
import java.util.HashMap;
import java.util.Map;
import java.util.function.Function;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 예치 확인({@link EscrowVerification#fundingOnChain})이 영수증·이벤트·현재 상태를 어떻게 읽는지.
 *
 * <p>진짜 노드 대신 JSON-RPC 흉내를 내는 로컬 HTTP 서버를 띄운다. 로그·eth_call 응답은 web3j ABI 인코더로
 * 만들어 실제 노드가 주는 것과 바이트 단위로 같다. 실제 체인에 대한 확인은 EscrowChainClientLiveTest 가 한다.
 */
class EscrowFundingVerificationTest {

    private static final String ESCROW = "0x1000000000000000000000000000000000000001";
    private static final String TOKEN = "0x2000000000000000000000000000000000000002";
    private static final String CLIENT = "0x3000000000000000000000000000000000000003";
    private static final String PROVIDER = "0x4000000000000000000000000000000000000004";
    private static final String OTHER = "0x5000000000000000000000000000000000000005";
    private static final String REF = "0x" + "ab".repeat(32);
    private static final String TX = "0x" + "cd".repeat(32);
    private static final BigInteger PRICE = TokenUnits.toWei(10);
    private static final String JOB_ID = "42";

    private final ObjectMapper mapper = new ObjectMapper();
    private final Map<String, Function<JsonNode, Object>> handlers = new HashMap<>();
    private HttpServer server;
    private EscrowVerification escrow;
    private EscrowChainClient chain;

    @BeforeEach
    void startFakeNode() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", exchange -> {
            JsonNode request = mapper.readTree(exchange.getRequestBody());
            Function<JsonNode, Object> handler = handlers.get(request.get("method").asString());
            var response = mapper.createObjectNode();
            response.put("jsonrpc", "2.0");
            response.set("id", request.get("id"));
            if (handler == null) {
                response.putObject("error").put("code", -32601).put("message", "method not found");
            } else {
                response.set("result", mapper.valueToTree(handler.apply(request.get("params"))));
            }
            byte[] body = mapper.writeValueAsBytes(response);
            exchange.getResponseHeaders().add("Content-Type", "application/json");
            exchange.sendResponseHeaders(200, body.length);
            try (OutputStream out = exchange.getResponseBody()) {
                out.write(body);
            }
        });
        server.start();
        EscrowProperties properties = new EscrowProperties(ESCROW,
                "http://127.0.0.1:" + server.getAddress().getPort(), TOKEN, true, null);
        chain = new EscrowChainClient(properties);
        escrow = new EscrowVerification(chain, properties);
    }

    @AfterEach
    void stopFakeNode() {
        server.stop(0);
    }

    // ── 정상 경로 ──────────────────────────────────────────────

    @Test
    @DisplayName("성공 영수증 + 에스크로의 JobFunded + 현재 FUNDED 면 CONFIRMED 이고 jobId 는 이벤트에서 온다")
    void confirmedWhenEverythingMatches() {
        receipt(true, ESCROW, jobFundedLog(ESCROW, JOB_ID, REF, PROVIDER, CLIENT, PRICE));
        jobs(EscrowJobState.FUNDED, CLIENT, PROVIDER, PRICE);

        EscrowVerification.FundingCheck check = escrow.fundingOnChain(TX, REF, CLIENT, PROVIDER, PRICE);

        assertThat(check.verdict()).isEqualTo(Verdict.CONFIRMED);
        assertThat(check.event().escrowJobId()).isEqualTo(JOB_ID);
        assertThat(check.event().priceWei()).isEqualTo(PRICE);
        assertThat(check.event().clientIs(CLIENT)).isTrue();
    }

    @Test
    @DisplayName("주소 표기(체크섬)가 달라도 같은 주소로 본다")
    void addressComparisonIgnoresCase() {
        receipt(true, ESCROW.toUpperCase().replace("0X", "0x"),
                jobFundedLog(ESCROW, JOB_ID, REF, PROVIDER, CLIENT, PRICE));
        jobs(EscrowJobState.FUNDED, CLIENT, PROVIDER, PRICE);

        assertThat(escrow.fundingOnChain(TX, REF.toUpperCase().replace("0X", "0x"),
                CLIENT.toUpperCase().replace("0X", "0x"), PROVIDER, PRICE).verdict())
                .isEqualTo(Verdict.CONFIRMED);
    }

    // ── 거부 ───────────────────────────────────────────────────

    @Test
    @DisplayName("되돌려진 트랜잭션은 CONTRADICTED")
    void revertedTransactionIsContradicted() {
        receipt(false, ESCROW, jobFundedLog(ESCROW, JOB_ID, REF, PROVIDER, CLIENT, PRICE));

        EscrowVerification.FundingCheck check = escrow.fundingOnChain(TX, REF, CLIENT, PROVIDER, PRICE);
        assertThat(check.verdict()).isEqualTo(Verdict.CONTRADICTED);
        assertThat(check.reason()).contains("reverted");
    }

    @Test
    @DisplayName("목적지가 에스크로가 아니면 CONTRADICTED — 다른 컨트랙트가 낸 JobFunded 는 예치가 아니다")
    void wrongDestinationIsContradicted() {
        receipt(true, OTHER, jobFundedLog(OTHER, JOB_ID, REF, PROVIDER, CLIENT, PRICE));

        assertThat(escrow.fundingOnChain(TX, REF, CLIENT, PROVIDER, PRICE).verdict())
                .isEqualTo(Verdict.CONTRADICTED);
    }

    @Test
    @DisplayName("에스크로에 보냈지만 그 안의 JobFunded 가 다른 컨트랙트 것뿐이면 CONTRADICTED")
    void foreignEventInsideEscrowCallIsContradicted() {
        receipt(true, ESCROW, jobFundedLog(OTHER, JOB_ID, REF, PROVIDER, CLIENT, PRICE));

        EscrowVerification.FundingCheck check = escrow.fundingOnChain(TX, REF, CLIENT, PROVIDER, PRICE);
        assertThat(check.verdict()).isEqualTo(Verdict.CONTRADICTED);
        assertThat(check.reason()).contains("no JobFunded");
    }

    @Test
    @DisplayName("externalRef 가 다르면 CONTRADICTED — 다른 작업의 예치를 제출한 것이다")
    void otherJobsRefIsContradicted() {
        receipt(true, ESCROW, jobFundedLog(ESCROW, JOB_ID, "0x" + "ef".repeat(32), PROVIDER, CLIENT, PRICE));

        assertThat(escrow.fundingOnChain(TX, REF, CLIENT, PROVIDER, PRICE).reason()).contains("externalRef");
    }

    @Test
    @DisplayName("client 가 로그인 사용자 지갑이 아니면 CONTRADICTED — 남의 트랜잭션")
    void someoneElsesTransactionIsContradicted() {
        receipt(true, ESCROW, jobFundedLog(ESCROW, JOB_ID, REF, PROVIDER, OTHER, PRICE));

        assertThat(escrow.fundingOnChain(TX, REF, CLIENT, PROVIDER, PRICE).reason()).contains("client");
    }

    @Test
    @DisplayName("provider 가 견적의 공급자 지갑이 아니면 CONTRADICTED")
    void wrongProviderIsContradicted() {
        receipt(true, ESCROW, jobFundedLog(ESCROW, JOB_ID, REF, OTHER, CLIENT, PRICE));

        assertThat(escrow.fundingOnChain(TX, REF, CLIENT, PROVIDER, PRICE).reason()).contains("provider");
    }

    @Test
    @DisplayName("가격이 다르면 CONTRADICTED — 1 wei 라도")
    void wrongPriceIsContradicted() {
        receipt(true, ESCROW, jobFundedLog(ESCROW, JOB_ID, REF, PROVIDER, CLIENT, PRICE.subtract(BigInteger.ONE)));

        assertThat(escrow.fundingOnChain(TX, REF, CLIENT, PROVIDER, PRICE).reason()).contains("price");
    }

    @Test
    @DisplayName("이벤트는 맞는데 지금 상태가 FUNDED 가 아니면(되찾아 감) CONTRADICTED")
    void refundedAfterFundingIsContradicted() {
        receipt(true, ESCROW, jobFundedLog(ESCROW, JOB_ID, REF, PROVIDER, CLIENT, PRICE));
        jobs(EscrowJobState.CANCELLED, CLIENT, PROVIDER, PRICE);

        EscrowVerification.FundingCheck check = escrow.fundingOnChain(TX, REF, CLIENT, PROVIDER, PRICE);
        assertThat(check.verdict()).isEqualTo(Verdict.CONTRADICTED);
        assertThat(check.reason()).contains("CANCELLED");
    }

    // ── 모름 ───────────────────────────────────────────────────

    @Test
    @DisplayName("영수증이 아직 없으면(채굴 전) UNKNOWN — 거부가 아니라 보류")
    void pendingTransactionIsUnknown() {
        handlers.put("eth_getTransactionReceipt", params -> null);

        EscrowVerification.FundingCheck check = escrow.fundingOnChain(TX, REF, CLIENT, PROVIDER, PRICE);
        assertThat(check.verdict()).isEqualTo(Verdict.UNKNOWN);
        assertThat(check.reason()).contains("not yet mined");
    }

    @Test
    @DisplayName("노드가 없으면 UNKNOWN")
    void unreachableNodeIsUnknown() {
        server.stop(0);

        assertThat(escrow.fundingOnChain(TX, REF, CLIENT, PROVIDER, PRICE).verdict()).isEqualTo(Verdict.UNKNOWN);
    }

    @Test
    @DisplayName("이벤트는 맞는데 jobs() 를 못 읽으면 UNKNOWN — 이벤트만으로 확정하지 않는다")
    void unreadableStateAfterEventIsUnknown() {
        receipt(true, ESCROW, jobFundedLog(ESCROW, JOB_ID, REF, PROVIDER, CLIENT, PRICE));
        // eth_call 핸들러 없음 → error 응답 → 비어 있음

        assertThat(escrow.fundingOnChain(TX, REF, CLIENT, PROVIDER, PRICE).verdict()).isEqualTo(Verdict.UNKNOWN);
    }

    @Test
    @DisplayName("정책: verification-required 면 UNKNOWN 도 막고, 아니면 CONTRADICTED 만 막는다")
    void policyDecidesWhatUnknownMeans() {
        assertThat(escrow.blocks(Verdict.UNKNOWN)).isTrue();
        assertThat(escrow.blocks(Verdict.CONTRADICTED)).isTrue();
        assertThat(escrow.blocks(Verdict.CONFIRMED)).isFalse();

        EscrowProperties relaxed = new EscrowProperties(ESCROW, "http://127.0.0.1:9", null, false, null);
        EscrowVerification local = new EscrowVerification(new EscrowChainClient(relaxed), relaxed);
        assertThat(local.blocks(Verdict.UNKNOWN)).isFalse();
        assertThat(local.blocks(Verdict.CONTRADICTED)).isTrue();
    }

    // ── 가짜 노드 응답 ───────────────────────────────────────────

    private void receipt(boolean success, String to, JsonNode... logs) {
        handlers.put("eth_getTransactionReceipt", params -> {
            var receipt = mapper.createObjectNode();
            receipt.put("status", success ? "0x1" : "0x0");
            receipt.put("to", to);
            receipt.put("transactionHash", TX);
            var array = receipt.putArray("logs");
            for (JsonNode log : logs) {
                array.add(log);
            }
            return receipt;
        });
    }

    /** JobFunded(uint256 indexed jobId, bytes32 indexed externalRef, address indexed provider, address client, uint256 price, uint64 deadline) */
    private JsonNode jobFundedLog(String address, String jobId, String externalRef, String provider,
                                  String client, BigInteger price) {
        var log = mapper.createObjectNode();
        log.put("address", address);
        var topics = log.putArray("topics");
        topics.add(EscrowChainClient.JOB_FUNDED_TOPIC);
        topics.add("0x" + TypeEncoder.encode(new Uint256(new BigInteger(jobId))));
        topics.add(externalRef.toLowerCase());
        topics.add("0x" + TypeEncoder.encode(new Address(provider)));
        log.put("data", "0x" + TypeEncoder.encode(new Address(client))
                + TypeEncoder.encode(new Uint256(price))
                + TypeEncoder.encode(new Uint64(BigInteger.valueOf(1_800_000_000L))));
        return log;
    }

    /** jobs(uint256) → (client, state, deadline, provider, operator, price, resultHash) */
    private void jobs(EscrowJobState state, String client, String provider, BigInteger price) {
        handlers.put("eth_call", params -> "0x"
                + TypeEncoder.encode(new Address(client))
                + TypeEncoder.encode(new Uint8(state.code()))
                + TypeEncoder.encode(new Uint64(BigInteger.valueOf(1_800_000_000L)))
                + TypeEncoder.encode(new Address(provider))
                + TypeEncoder.encode(new Address("0x0000000000000000000000000000000000000000"))
                + TypeEncoder.encode(new Uint256(price))
                + TypeEncoder.encode(new Bytes32(new byte[32])));
    }

}
