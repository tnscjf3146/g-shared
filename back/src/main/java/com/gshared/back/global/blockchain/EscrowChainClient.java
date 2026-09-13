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
import java.util.Collections;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicLong;

/**
 * 배포된 {@code GPUJobEscrow} 를 <b>읽기만</b> 하는 최소 RPC 창구.
 *
 * <p>Backend 가 체인에 트랜잭션을 보내는 일은 없다 -- 서명은 워커의 operator 키로만
 * 이뤄지고 그 키는 공급자 PC 밖으로 나가지 않는다. 여기서 하는 일은 워커가 보낸
 * 말을 체인에 대고 확인하는 것뿐이다. 그래서 {@code eth_call} 과
 * {@code eth_getLogs} 두 가지만 쓴다.
 *
 * <p><b>의존성을 새로 들이지 않았다.</b> ABI 인코딩은 이미 서명 검증 때문에 들어와
 * 있던 {@code org.web3j:abi} 를 쓰고, HTTP 는 JDK 의 {@link HttpClient} 를 쓴다.
 * {@code web3j:core} 를 넣으면 okhttp·rxjava 가 따라 들어오는데, 읽기 두 종류를
 * 위해 그만한 것을 들일 이유가 없다.
 *
 * <p><b>설정되지 않았으면 아무것도 하지 않는다.</b> {@code escrow.rpc-url} 이나
 * {@code escrow.contract-address} 가 비어 있으면 {@link #isConfigured()} 가 거짓이고,
 * 호출부는 예전과 똑같이 동작한다. 컨트랙트가 아직 배포되지 않은 지금 상태에서
 * 검증을 "켜진 척" 하게 만들지 않기 위해서다 -- 대조할 대상이 없는데 통과했다고
 * 기록하는 것이 확인하지 않는 것보다 나쁘다.
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
    private static final String JOB_FUNDED_TOPIC = "0x" + Hash.sha3String(
            "JobFunded(uint256,bytes32,address,address,uint256,uint64)").substring(2);

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
        return properties.rpcUrlOrNull() != null && properties.contractAddressOrNull() != null;
    }

    /**
     * {@code jobs(jobId)} 한 줄. 읽지 못했으면 비어 있다.
     *
     * <p>비어 있는 것과 {@link EscrowJobState#NONE} 은 다르다. 앞은 "노드에 묻지
     * 못했다"이고 뒤는 "노드가 그런 Job 은 없다고 답했다"이다.
     */
    public Optional<OnChainJob> readJob(String escrowJobId) {
        if (!isConfigured() || escrowJobId == null || escrowJobId.isBlank()) {
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
        return ethCall(call).flatMap(values -> {
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
     * <p>프런트가 준 escrowJobId 를 정본으로 쓰지 않기 위한 것이다 -- 그 값은
     * 브라우저에서 온 숫자고, 이쪽은 체인이 그 예치에 실제로 붙인 번호다.
     *
     * <p>{@code externalRef} 는 indexed 라 노드가 걸러 준다. 로그가 하나가 아니면
     * 비어 있는 것을 돌려준다: 같은 ref 로 두 번 예치된 상황을 여기서 하나 골라
     * 넘기면, 어느 쪽 돈인지 모르는 채로 Job 이 진행된다.
     */
    public Optional<FundedJob> findFundedJob(String externalRef) {
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
            JsonNode entry = result.get(0);
            try {
                // topics[1] = indexed jobId. data 에는 client·price·deadline 이 들어 있다.
                BigInteger jobId = new BigInteger(
                        entry.get("topics").get(1).asString().substring(2), 16);
                String provider = "0x" + entry.get("topics").get(3).asString().substring(26);
                return Optional.of(new FundedJob(jobId.toString(), provider));
            } catch (RuntimeException e) {
                log.warn("[CHAIN] JobFunded 로그를 읽지 못했다: {}", e.toString());
                return Optional.empty();
            }
        });
    }

    // ── 내부 ────────────────────────────────────────────────────

    private Optional<List<Type>> ethCall(Function call) {
        var request = mapper.createObjectNode();
        request.put("to", properties.contractAddressOrNull());
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

    private Optional<JsonNode> rpc(String method, List<Object> params) {
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
            return result == null || result.isNull() ? Optional.empty() : Optional.of(result);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return Optional.empty();
        } catch (Exception e) {
            // 노드에 닿지 못했다. "검증 실패"가 아니라 "확인하지 못함"이다.
            log.warn("[CHAIN] {} 을 보내지 못했다: {}", method, e.toString());
            return Optional.empty();
        }
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

    /** {@code jobs()} 한 줄. 이름이 있는 이유는 위치로 읽지 않기 위해서다. */
    public record OnChainJob(String client, EscrowJobState state, long deadline,
                             String provider, String operator, BigInteger priceWei,
                             String resultHash) {

        /** 이 주소가 이 Job 의 operator 인가. 체크섬 표기가 달라도 같은 주소다. */
        public boolean operatorIs(String address) {
            return address != null && operator != null
                    && operator.equalsIgnoreCase(address);
        }

        /** 기록된 resultHash 가 이것인가. {@code 0x} 유무와 대소문자만 무시한다. */
        public boolean resultHashIs(String expected) {
            if (expected == null || resultHash == null) {
                return false;
            }
            return strip(resultHash).equals(strip(expected));
        }

        private static String strip(String hex) {
            String text = hex.trim().toLowerCase();
            return text.startsWith("0x") ? text.substring(2) : text;
        }
    }

    /** {@code JobFunded} 가 말하는, 체인이 이 예치에 붙인 번호. */
    public record FundedJob(String escrowJobId, String provider) {
    }

    /** 테스트가 빈 목록을 기대하는 자리에서 쓰는 상수. */
    static final List<Type> NOTHING = Collections.emptyList();
}
