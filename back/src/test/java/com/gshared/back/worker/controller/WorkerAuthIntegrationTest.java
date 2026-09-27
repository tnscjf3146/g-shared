package com.gshared.back.worker.controller;

import com.gshared.back.global.security.jwt.JwtProvider;
import com.gshared.back.user.domain.User;
import com.gshared.back.user.repository.UserRepository;
import com.gshared.back.worker.dto.request.AuthenticateWorkerRequest;
import com.gshared.back.worker.dto.request.RegisterWorkerRequest;
import com.gshared.back.worker.dto.request.UpdateBenchmarkRequest;
import com.gshared.back.worker.dto.request.UpdateOperatorRequest;
import com.gshared.back.worker.dto.response.RegisterWorkerResponse;
import com.gshared.back.worker.repository.WorkerRepository;
import com.gshared.back.worker.service.WorkerService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 사람 없이 워커 프로그램이 부르는 세 창구(§4 authenticate · §5 operator · §6 benchmark)를
 * 보안 필터까지 거쳐 확인한다. 로그인 JWT 가 아니라 워커 토큰으로 인증되는지가 핵심이다.
 *
 * <p>실패 코드가 규격(401/403)과 맞는지를 매번 확인하는 이유: 워커 클라이언트는 401/403 을
 * "재연결로 풀리지 않는 실패"로 읽고 멈춘다. 다른 코드를 주면 무한 재접속을 돌 수 있다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional
class WorkerAuthIntegrationTest {

    private static final String AUTHENTICATE_URL = "/api/workers/authenticate";
    private static final String OPERATOR = "0x" + "b".repeat(40);

    @Autowired
    private MockMvc mockMvc;
    @Autowired
    private UserRepository userRepository;
    @Autowired
    private WorkerRepository workerRepository;
    @Autowired
    private WorkerService workerService;
    @Autowired
    private ObjectMapper objectMapper;
    @Autowired
    private JwtProvider jwtProvider;

    private String workerId;
    private String workerToken;
    private String ownerAccessToken;

    @BeforeEach
    void setUp() {
        User owner = userRepository.save(User.createUser(
                "worker-" + UUID.randomUUID() + "@gshared.io", "encoded", "워커주인"));
        ownerAccessToken = jwtProvider.createAccessToken(owner.getId(), owner.getRole());

        // 원문 토큰은 등록 응답에서만 나오므로 서비스로 등록해 받아둔다 (DB엔 해시만 남는다)
        RegisterWorkerResponse registered = workerService.register(owner.getId(), registerRequest(OPERATOR));
        workerId = registered.workerId();
        workerToken = registered.workerToken();
    }

    private static RegisterWorkerRequest registerRequest(String operatorAddress) {
        return new RegisterWorkerRequest(operatorAddress, "0.1.0",
                new RegisterWorkerRequest.SystemInfo("Windows 11", true),
                new RegisterWorkerRequest.GpuInfo("RTX 4090", 24576),
                new RegisterWorkerRequest.BenchmarkInfo(1800.0, null, "transformer-bench-v1"));
    }

    private String operatorUrl(String id) {
        return "/api/workers/" + id + "/operator";
    }

    private String benchmarkUrl(String id) {
        return "/api/workers/" + id + "/benchmark";
    }

    // ── 등록 ────────────────────────────────────────────────────

    @Test
    @DisplayName("등록 응답은 operatorAddress 를 정규화(소문자)해 돌려주고 DB 에도 그렇게 남는다")
    void registerNormalizesOperator() {
        User owner = userRepository.save(User.createUser(
                "reg-" + UUID.randomUUID() + "@gshared.io", "encoded", "등록자"));
        String checksummed = "0x" + "AbCdEf".repeat(6) + "AbCd";   // 대소문자 섞인 42자

        RegisterWorkerResponse response = workerService.register(owner.getId(), registerRequest(checksummed));

        assertThat(response.operatorAddress()).isEqualTo(checksummed.toLowerCase());
        assertThat(workerRepository.findById(response.workerId()))
                .hasValueSatisfying(w -> {
                    assertThat(w.getOperatorAddress()).isEqualTo(checksummed.toLowerCase());
                    assertThat(w.getWslAvailable()).isTrue();
                    assertThat(w.getBenchmarkVersion()).isEqualTo("transformer-bench-v1");
                });
    }

    /**
     * 구버전 G-shared.exe 는 benchmark.peakVramMb 를 모른다. 이 등록이 400 이 되면 그 PC 는 일을 못 받는다 —
     * 그래서 이 필드는 선택이고, 없으면 NULL 로 남는다 (V21).
     */
    @Test
    @DisplayName("peakVramMb 없이(구버전 exe) 등록해도 201 — 값은 NULL 로 남는다")
    void registerWithoutPeakVramStillAccepted() throws Exception {
        String body = """
                {"operatorAddress":"%s","clientVersion":"0.1.0",
                 "system":{"os":"Windows 11","wslAvailable":true},
                 "gpu":{"gpuModel":"RTX 4070 Laptop","vramMb":8188},
                 "benchmark":{"benchmarkTokensPerSec":900.0,"benchmarkVersion":"transformer-bench-v1"}}
                """.formatted(OPERATOR);

        String workerId = objectMapper.readTree(mockMvc.perform(post("/api/workers/register")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerAccessToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString()).path("workerId").asString();

        assertThat(workerRepository.findById(workerId))
                .hasValueSatisfying(w -> assertThat(w.getBenchmarkPeakVramMb()).isNull());
    }

    @Test
    @DisplayName("새 exe 가 보내는 benchmark.peakVramMb 는 그대로 저장된다")
    void registerStoresPeakVram() {
        User owner = userRepository.save(User.createUser(
                "peak-" + UUID.randomUUID() + "@gshared.io", "encoded", "등록자"));

        RegisterWorkerResponse response = workerService.register(owner.getId(),
                new RegisterWorkerRequest(OPERATOR, "0.2.0",
                        new RegisterWorkerRequest.SystemInfo("Windows 11", true),
                        new RegisterWorkerRequest.GpuInfo("NVIDIA GeForce RTX 4050 Laptop GPU", 6141),
                        new RegisterWorkerRequest.BenchmarkInfo(900.0, 5878, "transformer-bench-v1")));

        assertThat(workerRepository.findById(response.workerId()))
                .hasValueSatisfying(w -> assertThat(w.getBenchmarkPeakVramMb()).isEqualTo(5878));
    }

    @Test
    @DisplayName("benchmark.peakVramMb 가 0 이하면 400 — 없는 것은 되지만 틀린 값은 안 된다")
    void registerRejectsNonPositivePeakVram() throws Exception {
        String body = """
                {"operatorAddress":"%s","clientVersion":"0.2.0",
                 "system":{"os":"Windows 11","wslAvailable":true},
                 "gpu":{"gpuModel":"RTX 4070 Laptop","vramMb":8188},
                 "benchmark":{"benchmarkTokensPerSec":900.0,"peakVramMb":0,"benchmarkVersion":"transformer-bench-v1"}}
                """.formatted(OPERATOR);

        mockMvc.perform(post("/api/workers/register")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerAccessToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isBadRequest());
    }

    // ── POST /api/workers/authenticate ──────────────────────────

    @Test
    @DisplayName("workerId + workerToken 이 맞으면 200 과 authenticated=true")
    void authenticate() throws Exception {
        mockMvc.perform(post(AUTHENTICATE_URL)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(new AuthenticateWorkerRequest(workerId, workerToken))))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.workerId").value(workerId))
                .andExpect(jsonPath("$.authenticated").value(true));
    }

    @Test
    @DisplayName("모르는 토큰이면 401 INVALID_WORKER_TOKEN — 워커는 재연결하지 않고 멈춘다")
    void authenticateWithUnknownToken() throws Exception {
        mockMvc.perform(post(AUTHENTICATE_URL)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(
                                new AuthenticateWorkerRequest(workerId, UUID.randomUUID().toString()))))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("INVALID_WORKER_TOKEN"));
    }

    @Test
    @DisplayName("토큰은 유효해도 다른 워커를 주장하면 403 WORKER_FORBIDDEN")
    void authenticateWithMismatchedWorkerId() throws Exception {
        mockMvc.perform(post(AUTHENTICATE_URL)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(
                                new AuthenticateWorkerRequest("worker-" + UUID.randomUUID(), workerToken))))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.code").value("WORKER_FORBIDDEN"));
    }

    @Test
    @DisplayName("본문에 토큰이 없으면 400 — 규격상 토큰은 헤더가 아니라 본문이다")
    void authenticateWithoutToken() throws Exception {
        mockMvc.perform(post(AUTHENTICATE_URL)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"workerId\":\"" + workerId + "\"}"))
                .andExpect(status().isBadRequest());
    }

    // ── PUT /api/workers/{workerId}/operator ────────────────────

    @Test
    @DisplayName("operator 주소를 바꾸면 정규화된 주소와 updated=true 를 돌려준다")
    void updateOperator() throws Exception {
        String newOperator = "0x" + "ABCDEF".repeat(6) + "ABCD";

        mockMvc.perform(put(operatorUrl(workerId))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + workerToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(new UpdateOperatorRequest(newOperator))))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.workerId").value(workerId))
                .andExpect(jsonPath("$.operatorAddress").value(newOperator.toLowerCase()))
                .andExpect(jsonPath("$.updated").value(true));

        assertThat(workerRepository.findById(workerId))
                .hasValueSatisfying(w -> assertThat(w.getOperatorAddress()).isEqualTo(newOperator.toLowerCase()));
    }

    @Test
    @DisplayName("주소 형식이 틀리면 400 INVALID_OPERATOR_ADDRESS — 잘못된 주소로는 acceptJob 을 부를 수 없다")
    void updateOperatorWithInvalidAddress() throws Exception {
        mockMvc.perform(put(operatorUrl(workerId))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + workerToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(new UpdateOperatorRequest("0x1234"))))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("INVALID_OPERATOR_ADDRESS"));
    }

    @Test
    @DisplayName("Authorization 헤더가 없으면 401 — 인증 시도 자체가 없었던 것이다")
    void updateOperatorWithoutHeader() throws Exception {
        mockMvc.perform(put(operatorUrl(workerId))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(new UpdateOperatorRequest(OPERATOR))))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("INVALID_WORKER_TOKEN"));
    }

    // ── PUT /api/workers/{workerId}/benchmark ───────────────────

    @Test
    @DisplayName("벤치마크를 갱신하면 updated=true 와 함께 저장된 스펙이 바뀐다")
    void updateBenchmark() throws Exception {
        mockMvc.perform(put(benchmarkUrl(workerId))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + workerToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(new UpdateBenchmarkRequest(
                                "RTX 5090", 32768, 2600.5, 21000, "transformer-bench-v2"))))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.workerId").value(workerId))
                .andExpect(jsonPath("$.updated").value(true));

        assertThat(workerRepository.findById(workerId)).hasValueSatisfying(worker -> {
            assertThat(worker.getGpuModel()).isEqualTo("RTX 5090");
            assertThat(worker.getVramMb()).isEqualTo(32768);
            assertThat(worker.getBenchmarkTokensPerSec()).isEqualTo(2600.5);
            assertThat(worker.getBenchmarkPeakVramMb()).isEqualTo(21000);
            assertThat(worker.getBenchmarkVersion()).isEqualTo("transformer-bench-v2");
        });
    }

    @Test
    @DisplayName("peakVramMb 없이(구버전 exe) 벤치마크를 갱신해도 200 — 이전 실측은 남기지 않고 NULL 로 덮는다")
    void updateBenchmarkWithoutPeakVram() throws Exception {
        workerService.updateBenchmark(workerToken, workerId, new UpdateBenchmarkRequest(
                "RTX 4090", 24576, 1800.0, 20000, "transformer-bench-v1"));

        mockMvc.perform(put(benchmarkUrl(workerId))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + workerToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"gpuModel":"RTX 5090","vramMb":32768,
                                 "benchmarkTokensPerSec":2600.5,"benchmarkVersion":"transformer-bench-v1"}
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.updated").value(true));

        // 카드가 바뀌었을 수 있으니 옛 카드의 실측을 새 카드 값처럼 두지 않는다
        assertThat(workerRepository.findById(workerId))
                .hasValueSatisfying(w -> assertThat(w.getBenchmarkPeakVramMb()).isNull());
    }

    @Test
    @DisplayName("남의 워커 벤치마크는 갱신할 수 없다 — 403")
    void updateBenchmarkOfAnotherWorker() throws Exception {
        mockMvc.perform(put(benchmarkUrl("worker-" + UUID.randomUUID()))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + workerToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(new UpdateBenchmarkRequest(
                                "RTX 5090", 32768, 2600.5, null, "transformer-bench-v1"))))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.code").value("WORKER_FORBIDDEN"));

        // 내 워커 스펙도 그대로여야 한다
        assertThat(workerRepository.findById(workerId))
                .hasValueSatisfying(worker -> assertThat(worker.getGpuModel()).isEqualTo("RTX 4090"));
    }

    @Test
    @DisplayName("벤치마크 값이 0 이면 400 — 스케줄러가 빈 성능치를 그대로 믿게 두지 않는다")
    void updateBenchmarkWithInvalidValue() throws Exception {
        mockMvc.perform(put(benchmarkUrl(workerId))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + workerToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(new UpdateBenchmarkRequest(
                                "RTX 5090", 32768, 0.0, null, "transformer-bench-v1"))))
                .andExpect(status().isBadRequest());
    }
}
