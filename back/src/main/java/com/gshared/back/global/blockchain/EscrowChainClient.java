package com.gshared.back.global.blockchain;

import tools.jackson.databind.JsonNode;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.web3j.abi.FunctionEncoder;
import org.web3j.abi.FunctionReturnDecoder;
import org.web3j.abi.TypeReference;
import org.web3j.abi.datatypes.Address;
import org.web3j.abi.datatypes.Bool;
import org.web3j.abi.datatypes.Function;
import org.web3j.abi.datatypes.Type;
import org.web3j.abi.datatypes.generated.Bytes32;
import org.web3j.abi.datatypes.generated.Uint256;
import org.web3j.abi.datatypes.generated.Uint64;
import org.web3j.abi.datatypes.generated.Uint8;
import org.web3j.crypto.Hash;
import tools.jackson.databind.ObjectMapper;

import java.math.BigInteger;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicLong;

/**
 * 배포된 {@code GPUJobEscrow} 에 닿는 최소 RPC 창구.
 *
 * <p>Backend 가 하는 일은 대부분 <b>읽기</b>다 -- 워커·프런트가 보낸 말을 체인에 대고
 * 확인한다({@code eth_call} · {@code eth_getLogs} · {@code eth_getTransactionReceipt}).
 * 쓰기는 딱 하나, 만료 Job 을 정리하는 {@code settleTimeout} 뿐이고 그것도 전용
 * keeper 키로만 보낸다({@link #sendRawTransaction}). acceptJob·submitResult 같은
 * 정산 경로의 서명은 워커의 operator 키로만 이뤄지고 그 키는 공급자 PC 밖으로
 * 나가지 않는다.
 *
 * <p><b>의존성을 새로 들이지 않았다.</b> ABI 인코딩은 이미 서명 검증 때문에 들어와
 * 있던 {@code org.web3j:abi} 를 쓰고, HTTP 는 JDK 의 {@link HttpClient} 를 쓴다.
 * {@code web3j:core} 를 넣으면 okhttp·rxjava 가 따라 들어오는데, 이만한 호출을
 * 위해 그만한 것을 들일 이유가 없다.
 *
 * <p><b>설정되지 않았으면 아무것도 하지 않는다.</b> {@code escrow.rpc-url} 이나
 * {@code escrow.contract-address} 가 비어 있으면 {@link #isConfigured()} 가 거짓이고,
 * 호출부는 체인 없이 동작한다(운영에서는 {@code escrow.verification-required} 가
 * 그것을 막는다).
 *
 * <p><b>모르는 것과 틀린 것을 구분한다.</b> 노드가 답하지 않으면
 * {@link Optional#empty()} 이고, 그것은 "검증 실패"가 아니라 "확인하지 못함"이다.
 * 호출부는 그 둘을 다르게 처리해야 한다 -- RPC 장애를 근거로 Job 을 종료 상태로
 * 확정하면, 다음 날 체인을 보고 그것이 거짓이었음을 알게 된다.
 */
@Slf4j
@Component
public class EscrowChainClient {

    /** {@code jobs(uint256)} 가 돌려주는 일곱 값의 순서. 위치가 계약이다. */
    private static final List<TypeReference<?>> JOB_OUTPUTS = List.of(
            new TypeReference<Address>() { },    // client
            new TypeReference<Uint8>() { },      // state
            new TypeReference<Uint64>() { },     // deadline
            new TypeReference<Address>() { },    // provider
            new TypeReference<Address>() { },    // operator
            new TypeReference<Uint256>() { },    // price
            new TypeReference<Bytes32>() { });   // resultHash

    /** {@code JobFunded(uint256,bytes32,address,address,uint256,uint64)} 의 topic0. */
    static final String JOB_FUNDED_TOPIC = "0x" + Hash.sha3String(
            "JobFunded(uint256,bytes32,address,address,uint256,uint64)").substring(2);

    /** JobFunded 의 non-indexed 부분(data): client, price, deadline. */
    private static final List<TypeReference<Type>> JOB_FUNDED_DATA = List.of(
            typeRef(new TypeReference<Address>() { }),
            typeRef(new TypeReference<Uint256>() { }),
            typeRef(new TypeReference<Uint64>() { }));

    private static final Duration TIMEOUT = Duration.ofSeconds(10);

    private final EscrowProperties properties;
    private final HttpClient http;
    private final ObjectMapper mapper = new ObjectMapper();
    private final AtomicLong requestId = new AtomicLong(1);

    public EscrowChainClient(EscrowProperties properties) {
        this.properties = properties;
        this.http = HttpClient.newBuilder()
                // HTTP/1.1 을 못 박는다. JDK 기본값은 HTTP_2 이고, 평문 http:// 로는
                // 먼저 h2c 업그레이드를 시도하는데 -- 이더리움 노드는 그 요청을
                // 405 로 되돌린다(hardhat 에서 실제로 확인했다). 그러면 모든 조회가
                // '노드가 답하지 않음'이 되어 검증이 통째로 UNKNOWN 으로 접히고,
                // 아무도 막히지 않으니 아무도 눈치채지 못한다.
                .version(HttpClient.Version.HTTP_1_1)
                .connectTimeout(TIMEOUT)
                .build();
    }

    /** 대조할 체인이 있는가. 없으면 호출부는 검증을 건너뛴다. */
    public boolean isConfigured() {
        return properties.isConfigured();
    }

    /** 지금 설정된 에스크로 주소. 없으면 null. Job 에 기록해 재배포 뒤에도 같은 컨트랙트를 읽게 한다. */
    public String contractAddress() {
        return properties.contractAddressOrNull();
    }

    // ── 기동 검사용 ─────────────────────────────────────────────

    /** {@code eth_chainId}. 노드가 어느 체인인지 -- 설정과 다르면 모든 대조가 엉뚱한 체인에 대고 이뤄진다. */
    public Optional<Long> chainId() {
        return rpc("eth_chainId", List.of())
                .flatMap(this::hexQuantity)
                .map(BigInteger::longValue);
    }

    /** 에스크로 주소에 코드가 있는가. 없으면 그 주소로 보내는 모든 eth_call 이 "0x" 를 돌려준다. */
    public Optional<Boolean> contractHasCode() {
        if (!isConfigured()) {
            return Optional.empty();
        }
        return rpc("eth_getCode", List.of(properties.contractAddressOrNull(), "latest"))
                .map(result -> result.isString() && result.asString().length() > 2);
    }

    /** {@code paymentToken()}. 에스크로가 정산에 쓰는 토큰 -- 설정한 GSC 주소와 같아야 한다. */
    public Optional<String> paymentToken() {
        if (!isConfigured()) {
            return Optional.empty();
        }
        Function call = new Function("paymentToken", List.of(), List.of(new TypeReference<Address>() { }));
        return ethCall(call).map(values -> values.get(0).getValue().toString());
    }

    // ── 읽기 ───────────────────────────────────────────────────

    /**
     * {@code jobs(jobId)} 한 줄. 읽지 못했으면 비어 있다.
     *
     * <p>비어 있는 것과 {@link EscrowJobState#NONE} 은 다르다. 앞은 "노드에 묻지
     * 못했다"이고 뒤는 "노드가 그런 Job 은 없다고 답했다"이다.
     */
    public Optional<OnChainJob> readJob(String escrowJobId) {
        return readJob(properties.contractAddressOrNull(), escrowJobId);
    }

    /**
     * 특정 에스크로 주소의 {@code jobs(jobId)}. Job 에 기록된 주소로 읽을 때 쓴다 --
     * 재배포 뒤에도 예전 Job 은 예전 컨트랙트에 있다.
     */
    public Optional<OnChainJob> readJob(String contractAddress, String escrowJobId) {
        if (!isConfigured() || isBlank(contractAddress) || escrowJobId == null || escrowJobId.isBlank()) {
            return Optional.empty();
        }
        BigInteger jobId;
        try {
            jobId = new BigInteger(escrowJobId.trim());
        } catch (NumberFormatException e) {
            log.warn("[CHAIN] escrowJobId 가 10진 정수가 아니다: {}", escrowJobId);
            return Optional.empty();
        }

        Function call = new Function("jobs", List.of(new Uint256(jobId)), JOB_OUTPUTS);
        return ethCall(contractAddress, call).flatMap(values -> {
            if (values.size() != JOB_OUTPUTS.size()) {
                log.warn("[CHAIN] jobs() 가 {} 개를 돌려줬다 (기대 {})", values.size(), JOB_OUTPUTS.size());
                return Optional.empty();
            }
            try {
                return Optional.of(new OnChainJob(
                        values.get(0).getValue().toString(),
                        EscrowJobState.of(((BigInteger) values.get(1).getValue()).intValue()),
                        ((BigInteger) values.get(2).getValue()).longValue(),
                        values.get(3).getValue().toString(),
                        values.get(4).getValue().toString(),
                        (BigInteger) values.get(5).getValue(),
                        toHex((byte[]) values.get(6).getValue())));
            } catch (RuntimeException e) {
                log.warn("[CHAIN] jobs() 응답을 읽지 못했다: {}", e.toString());
                return Optional.empty();
            }
        });
    }

    /**
     * {@code isOperator(provider, operator)}.
     *
     * <p>승인 여부를 <b>모를</b> 수도 있으므로 boolean 이 아니라 Optional 이다.
     * 노드가 답하지 않았을 때 거짓으로 접으면, 멀쩡한 공급자가 RPC 장애 동안
     * 후보에서 조용히 빠진다.
     */
    public Optional<Boolean> isOperator(String provider, String operator) {
        if (!isConfigured() || isBlank(provider) || isBlank(operator)) {
            return Optional.empty();
        }
        Function call = new Function("isOperator",
                List.of(new Address(provider), new Address(operator)),
                List.of(new TypeReference<Bool>() { }));
        return ethCall(call).flatMap(values -> values.isEmpty()
                ? Optional.empty()
                : Optional.of((Boolean) values.get(0).getValue()));
    }

    /**
     * {@code externalRef} 로 {@code JobFunded} 로그를 찾아 온체인 jobId 를 얻는다.
     *
     * <p>txHash 없이 externalRef 만으로 예치를 찾아야 할 때(reconciliation) 쓴다.
     * 예치 확인 API 는 영수증({@link #readReceipt}) 을 쓴다 -- 그쪽이 "이 트랜잭션이
     * 이 예치다"까지 못 박는다.
     *
     * <p>{@code externalRef} 는 indexed 라 노드가 걸러 준다. 로그가 하나가 아니면
     * 비어 있는 것을 돌려준다: 같은 ref 로 두 번 예치된 상황을 여기서 하나 골라
     * 넘기면, 어느 쪽 돈인지 모르는 채로 Job 이 진행된다.
     */
    public Optional<JobFundedEvent> findFundedJob(String externalRef) {
        if (!isConfigured() || isBlank(externalRef)) {
            return Optional.empty();
        }
        var filter = mapper.createObjectNode();
        filter.put("fromBlock", "0x0");
        filter.put("toBlock", "latest");
        filter.put("address", properties.contractAddressOrNull());
        var topics = filter.putArray("topics");
        topics.add(JOB_FUNDED_TOPIC);
        topics.addNull();                    // jobId — 무엇이든
        topics.add(normaliseRef(externalRef));

        return rpc("eth_getLogs", List.of(filter)).flatMap(result -> {
            if (!result.isArray() || result.size() != 1) {
                log.warn("[CHAIN] externalRef 에 대한 JobFunded 로그가 {} 건이다",
                        result.isArray() ? result.size() : -1);
                return Optional.empty();
            }
            return parseJobFunded(result.get(0));
        });
    }

    /**
     * {@code eth_getTransactionReceipt}. 예치 확인 API 가 프런트가 준 txHash 를 대조하는 근거.
     *
     * <p>세 갈래다: 노드가 답하지 않음 / 노드는 답했는데 그런 트랜잭션이 (아직) 없음 /
     * 영수증 있음. 둘째를 첫째와 섞으면 "아직 채굴 안 됨"이 RPC 장애처럼 보이고,
     * 셋째와 섞으면 없는 트랜잭션이 통과한다.
     */
    public ReceiptLookup readReceipt(String txHash) {
        if (!isConfigured() || isBlank(txHash)) {
            return ReceiptLookup.unreachable();
        }
        return rpcAllowingNull("eth_getTransactionReceipt", List.of(txHash.trim().toLowerCase()))
                .map(result -> {
                    if (result.isNull()) {
                        return ReceiptLookup.notFound();
                    }
                    try {
                        boolean success = hexQuantity(result.get("status"))
                                .map(status -> status.equals(BigInteger.ONE)).orElse(false);
                        JsonNode to = result.get("to");
                        List<JsonNode> logs = new ArrayList<>();
                        JsonNode logArray = result.get("logs");
                        if (logArray != null && logArray.isArray()) {
                            logArray.forEach(logs::add);
                        }
                        return ReceiptLookup.found(new Receipt(
                                success,
                                to == null || to.isNull() ? null : to.asString(),
                                logs));
                    } catch (RuntimeException e) {
                        log.warn("[CHAIN] 영수증을 읽지 못했다: {}", e.toString());
                        return ReceiptLookup.unreachable();
                    }
                })
                .orElseGet(ReceiptLookup::unreachable);
    }

    /**
     * 영수증 안에서 <b>이 에스크로가 낸</b> {@code JobFunded} 를 찾는다.
     *
     * <p>주소를 대조하는 이유: 같은 시그니처의 이벤트는 누구나 낼 수 있다. 다른
     * 컨트랙트가 낸 JobFunded 를 근거로 FUNDED 를 확정하면 예치 없이 매칭이 돈다.
     * 정확히 하나여야 한다 -- createAndFundJob 은 한 번에 하나만 낸다.
     */
    public Optional<JobFundedEvent> jobFundedIn(Receipt receipt) {
        String escrow = properties.contractAddressOrNull();
        if (receipt == null || escrow == null) {
            return Optional.empty();
        }
        List<JsonNode> matched = receipt.logs().stream()
                .filter(entry -> escrow.equalsIgnoreCase(text(entry.get("address"))))
                .filter(entry -> {
                    JsonNode topics = entry.get("topics");
                    return topics != null && topics.isArray() && topics.size() == 4
                            && JOB_FUNDED_TOPIC.equalsIgnoreCase(text(topics.get(0)));
                })
                .toList();
        if (matched.size() != 1) {
            log.warn("[CHAIN] 영수증에 에스크로의 JobFunded 가 {} 건이다", matched.size());
            return Optional.empty();
        }
        return parseJobFunded(matched.get(0));
    }

    /** 최신 블록의 timestamp(초). keeper 가 deadline 을 비교할 때 서버 시계 대신 쓴다. */
    public Optional<Long> latestBlockTimestamp() {
        return rpc("eth_getBlockByNumber", List.of("latest", false))
                .flatMap(block -> hexQuantity(block.get("timestamp")))
                .map(BigInteger::longValue);
    }

    // ── 쓰기 (keeper 전용) ──────────────────────────────────────

    /** {@code eth_getTransactionCount(address, "pending")}. 서명 전에 nonce 를 정한다. */
    public Optional<BigInteger> pendingNonce(String address) {
        return rpc("eth_getTransactionCount", List.of(address, "pending")).flatMap(this::hexQuantity);
    }

    /** {@code eth_gasPrice}. SSAFY 체인은 0 이지만 노드가 말하는 값을 쓴다. */
    public Optional<BigInteger> gasPrice() {
        return rpc("eth_gasPrice", List.of()).flatMap(this::hexQuantity);
    }

    /** {@code settleTimeout(uint256)} 호출 데이터. 서명은 호출부(keeper)가 한다. */
    public String encodeSettleTimeout(String escrowJobId) {
        return FunctionEncoder.encode(new Function("settleTimeout",
                List.of(new Uint256(new BigInteger(escrowJobId.trim()))), List.of()));
    }

    /** {@code eth_sendRawTransaction}. 성공하면 txHash. */
    public Optional<String> sendRawTransaction(String signedHex) {
        return rpc("eth_sendRawTransaction", List.of(signedHex))
                .filter(JsonNode::isString)
                .map(JsonNode::asString);
    }

    // ── 내부 ────────────────────────────────────────────────────

    private Optional<JobFundedEvent> parseJobFunded(JsonNode entry) {
        try {
            JsonNode topics = entry.get("topics");
            // topics[1] = indexed jobId, [2] = externalRef, [3] = provider. data = client·price·deadline.
            BigInteger jobId = new BigInteger(text(topics.get(1)).substring(2), 16);
            String externalRef = text(topics.get(2)).toLowerCase();
            String provider = "0x" + text(topics.get(3)).substring(26).toLowerCase();
            List<Type> data = FunctionReturnDecoder.decode(text(entry.get("data")), JOB_FUNDED_DATA);
            if (data.size() != 3) {
                log.warn("[CHAIN] JobFunded data 가 {} 개다 (기대 3)", data.size());
                return Optional.empty();
            }
            return Optional.of(new JobFundedEvent(
                    jobId.toString(),
                    externalRef,
                    provider,
                    data.get(0).getValue().toString().toLowerCase(),
                    (BigInteger) data.get(1).getValue(),
                    ((BigInteger) data.get(2).getValue()).longValue()));
        } catch (RuntimeException e) {
            log.warn("[CHAIN] JobFunded 로그를 읽지 못했다: {}", e.toString());
            return Optional.empty();
        }
    }

    private Optional<List<Type>> ethCall(Function call) {
        return ethCall(properties.contractAddressOrNull(), call);
    }

    private Optional<List<Type>> ethCall(String to, Function call) {
        var request = mapper.createObjectNode();
        request.put("to", to);
        request.put("data", FunctionEncoder.encode(call));

        return rpc("eth_call", List.of(request, "latest")).flatMap(result -> {
            String hex = result.isString() ? result.asString() : null;
            if (hex == null || hex.length() <= 2) {
                // "0x" — 그런 주소에 코드가 없거나 호출이 되돌려졌다.
                log.warn("[CHAIN] eth_call 이 빈 답을 줬다 ({})", call.getName());
                return Optional.empty();
            }
            List<Type> values = FunctionReturnDecoder.decode(hex, call.getOutputParameters());
            return values.isEmpty() ? Optional.empty() : Optional.of(values);
        });
    }

    /** result 가 JSON null 이면 비어 있다 -- "노드가 없다고 답함"을 "못 물어봄"과 같이 다룬다. */
    private Optional<JsonNode> rpc(String method, List<Object> params) {
        return rpcAllowingNull(method, params).filter(result -> !result.isNull());
    }

    /** result 가 JSON null 이어도 {@code Optional.of(NullNode)} 다. 비어 있는 것은 노드에 닿지 못한 것뿐이다. */
    private Optional<JsonNode> rpcAllowingNull(String method, List<Object> params) {
        var body = mapper.createObjectNode();
        body.put("jsonrpc", "2.0");
        body.put("id", requestId.getAndIncrement());
        body.put("method", method);
        body.set("params", mapper.valueToTree(params));

        try {
            HttpRequest request = HttpRequest.newBuilder(URI.create(properties.rpcUrlOrNull()))
                    .timeout(TIMEOUT)
                    .header("Content-Type", "application/json")
                    .POST(HttpRequest.BodyPublishers.ofString(
                            mapper.writeValueAsString(body), StandardCharsets.UTF_8))
                    .build();
            HttpResponse<String> response = http.send(request, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() / 100 != 2) {
                log.warn("[CHAIN] {} -> HTTP {}", method, response.statusCode());
                return Optional.empty();
            }
            JsonNode answered = mapper.readTree(response.body());
            if (answered.has("error")) {
                log.warn("[CHAIN] {} -> {}", method, answered.get("error").toString());
                return Optional.empty();
            }
            JsonNode result = answered.get("result");
            return result == null ? Optional.empty() : Optional.of(result);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return Optional.empty();
        } catch (Exception e) {
            // 노드에 닿지 못했다. "검증 실패"가 아니라 "확인하지 못함"이다.
            log.warn("[CHAIN] {} 을 보내지 못했다: {}", method, e.toString());
            return Optional.empty();
        }
    }

    private Optional<BigInteger> hexQuantity(JsonNode node) {
        if (node == null || !node.isString()) {
            return Optional.empty();
        }
        String hex = node.asString().trim().toLowerCase();
        if (hex.startsWith("0x")) {
            hex = hex.substring(2);
        }
        if (hex.isEmpty()) {
            return Optional.empty();
        }
        try {
            return Optional.of(new BigInteger(hex, 16));
        } catch (NumberFormatException e) {
            return Optional.empty();
        }
    }

    private static String text(JsonNode node) {
        return node == null || node.isNull() ? "" : node.asString();
    }

    private static String normaliseRef(String externalRef) {
        String text = externalRef.trim().toLowerCase();
        return text.startsWith("0x") ? text : "0x" + text;
    }

    private static String toHex(byte[] raw) {
        StringBuilder text = new StringBuilder("0x");
        for (byte one : raw) {
            text.append(String.format("%02x", one));
        }
        return text.toString();
    }

    private static boolean isBlank(String value) {
        return value == null || value.isBlank();
    }

    @SuppressWarnings("unchecked")
    private static TypeReference<Type> typeRef(TypeReference<?> ref) {
        return (TypeReference<Type>) ref;
    }

    /** {@code jobs()} 한 줄. 이름이 있는 이유는 위치로 읽지 않기 위해서다. */
    public record OnChainJob(String client, EscrowJobState state, long deadline,
                             String provider, String operator, BigInteger priceWei,
                             String resultHash) {

        /** 이 주소가 이 Job 의 operator 인가. 체크섬 표기가 달라도 같은 주소다. */
        public boolean operatorIs(String address) {
            return sameAddress(operator, address);
        }

        public boolean clientIs(String address) {
            return sameAddress(client, address);
        }

        public boolean providerIs(String address) {
            return sameAddress(provider, address);
        }

        /** 기록된 resultHash 가 이것인가. {@code 0x} 유무와 대소문자만 무시한다. */
        public boolean resultHashIs(String expected) {
            if (expected == null || resultHash == null) {
                return false;
            }
            return strip(resultHash).equals(strip(expected));
        }

        /** 이 상태에서 settleTimeout 을 부를 수 있는가(체인 시각 기준). */
        public boolean isExpiredAt(long chainTimestamp) {
            return (state == EscrowJobState.FUNDED || state == EscrowJobState.ASSIGNED)
                    && chainTimestamp > deadline;
        }

        private static String strip(String hex) {
            String text = hex.trim().toLowerCase();
            return text.startsWith("0x") ? text.substring(2) : text;
        }
    }

    /** {@code JobFunded} 한 건. 체인이 이 예치에 붙인 번호와 예치 당시 값들. 주소는 전부 소문자다. */
    public record JobFundedEvent(String escrowJobId, String externalRef, String provider,
                                 String client, BigInteger priceWei, long deadline) {

        public boolean externalRefIs(String expected) {
            return expected != null && externalRef.equalsIgnoreCase(
                    expected.startsWith("0x") ? expected : "0x" + expected);
        }

        public boolean clientIs(String address) {
            return sameAddress(client, address);
        }

        public boolean providerIs(String address) {
            return sameAddress(provider, address);
        }
    }

    /** {@code eth_getTransactionReceipt} 에서 필요한 것만. */
    public record Receipt(boolean success, String to, List<JsonNode> logs) {

        public boolean sentTo(String address) {
            return sameAddress(to, address);
        }
    }

    /** 영수증 조회의 세 갈래. */
    public record ReceiptLookup(Status status, Receipt receipt) {

        public enum Status { UNREACHABLE, NOT_FOUND, FOUND }

        static ReceiptLookup unreachable() {
            return new ReceiptLookup(Status.UNREACHABLE, null);
        }

        static ReceiptLookup notFound() {
            return new ReceiptLookup(Status.NOT_FOUND, null);
        }

        static ReceiptLookup found(Receipt receipt) {
            return new ReceiptLookup(Status.FOUND, receipt);
        }
    }

    static boolean sameAddress(String a, String b) {
        return a != null && b != null && a.equalsIgnoreCase(b);
    }

    /** 테스트가 빈 목록을 기대하는 자리에서 쓰는 상수. */
    static final List<Type> NOTHING = Collections.emptyList();
}
