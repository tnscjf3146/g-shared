package com.gshared.back.worker.controller;

import com.gshared.back.global.blockchain.EscrowChainClient;
import com.gshared.back.global.security.jwt.JwtProvider;
import com.gshared.back.user.domain.User;
import com.gshared.back.user.repository.UserRepository;
import com.gshared.back.worker.domain.Worker;
import com.gshared.back.worker.dto.request.RegisterWorkerRequest;
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
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.Optional;
import java.util.UUID;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.BDDMockito.given;
import static org.mockito.BDDMockito.then;
import static org.mockito.Mockito.never;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * "내 워커가 ONLINE 인데 왜 Job 이 안 오나"에 답하는 두 창구 — operator-status 와 /me 의 availability.
 *
 * <p>체인은 대역이다. 확인하려는 것은 컨트랙트가 아니라 "어느 주소 둘을 묻고, 답을 어떤 상태로
 * 옮기는가"다. providerAddress 는 반드시 {@code users.wallet_address}, operatorAddress 는 반드시
 * {@code workers.operator_address} 여야 한다 — 이 둘이 섞이면 승인이 영원히 false 다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional
class WorkerOperatorStatusIntegrationTest {

    private static final String MINE_URL = "/api/workers/me";
    private static final String PROVIDER_WALLET = "0x9cb91880cd04a6b3bc8cb81d908fe19060260af9";
    private static final String OPERATOR = "0x0394494f68abc014e42879563156b7b19b6fe815";

    @Autowired
    private MockMvc mockMvc;
    @Autowired
    private UserRepository userRepository;
    @Autowired
    private WorkerRepository workerRepository;
    @Autowired
    private WorkerService workerService;
    @Autowired
    private JwtProvider jwtProvider;

    @MockitoBean
    private EscrowChainClient chain;

    private User owner;
    private String ownerToken;
    private String workerId;

    @BeforeEach
    void setUp() {
        owner = userRepository.save(User.createUser(
                "opstatus-" + UUID.randomUUID() + "@gshared.io", "encoded", "공급자"));
        ownerToken = jwtProvider.createAccessToken(owner.getId(), owner.getRole());
        owner.linkWallet(PROVIDER_WALLET);

        RegisterWorkerResponse registered = workerService.register(owner.getId(),
                new RegisterWorkerRequest(OPERATOR, "0.1.0",
                        new RegisterWorkerRequest.SystemInfo("Windows 11", true),
                        new RegisterWorkerRequest.GpuInfo("NVIDIA GeForce RTX 4070 Laptop GPU", 8188),
                        new RegisterWorkerRequest.BenchmarkInfo(900.0, "transformer-bench-v1")));
        workerId = registered.workerId();

        given(chain.isConfigured()).willReturn(true);
    }

    private String statusUrl(String id) {
        return "/api/workers/" + id + "/operator-status";
    }

    private void goOnline() {
        Worker worker = workerRepository.findById(workerId).orElseThrow();
        worker.goOnline(LocalDateTime.now());
        workerRepository.flush();
    }

    // ── GET /api/workers/{workerId}/operator-status ─────────────

    @Test
    @DisplayName("체인이 isOperator=true 라고 하면 approved=true — 물어보는 주소는 provider 지갑과 worker operator 다")
    void approved() throws Exception {
        given(chain.isOperator(PROVIDER_WALLET, OPERATOR)).willReturn(Optional.of(true));

        mockMvc.perform(get(statusUrl(workerId))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.workerId").value(workerId))
                .andExpect(jsonPath("$.providerAddress").value(PROVIDER_WALLET))
                .andExpect(jsonPath("$.operatorAddress").value(OPERATOR))
                .andExpect(jsonPath("$.approved").value(true))
                .andExpect(jsonPath("$.status").value("APPROVED"))
                // setOperator 를 보낼 체인 — wallet-auth.chain-id 와 같은 값
                .andExpect(jsonPath("$.chainId").value(31221));

        then(chain).should().isOperator(PROVIDER_WALLET, OPERATOR);
    }

    @Test
    @DisplayName("isOperator=false 면 approved=false, NOT_APPROVED — 공급자가 MetaMask 로 승인해야 한다")
    void notApproved() throws Exception {
        given(chain.isOperator(PROVIDER_WALLET, OPERATOR)).willReturn(Optional.of(false));

        mockMvc.perform(get(statusUrl(workerId))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.approved").value(false))
                .andExpect(jsonPath("$.status").value("NOT_APPROVED"));
    }

    @Test
    @DisplayName("승인 뒤 다시 물으면 true — 예전 답을 캐시하지 않고 매번 체인에 묻는다")
    void reflectsChainEachTime() throws Exception {
        given(chain.isOperator(PROVIDER_WALLET, OPERATOR)).willReturn(Optional.of(false));
        mockMvc.perform(get(statusUrl(workerId))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken))
                .andExpect(jsonPath("$.approved").value(false));

        given(chain.isOperator(PROVIDER_WALLET, OPERATOR)).willReturn(Optional.of(true));
        mockMvc.perform(get(statusUrl(workerId))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken))
                .andExpect(jsonPath("$.approved").value(true));
    }

    @Test
    @DisplayName("operator 주소를 바꾸면 새 주소로 묻는다 — 예전 키의 승인은 새 키에 이어지지 않는다")
    void asksWithCurrentOperator() throws Exception {
        String newOperator = "0x" + "d".repeat(40);
        Worker worker = workerRepository.findById(workerId).orElseThrow();
        worker.updateOperator(newOperator);
        workerRepository.flush();
        given(chain.isOperator(PROVIDER_WALLET, OPERATOR)).willReturn(Optional.of(true));
        given(chain.isOperator(PROVIDER_WALLET, newOperator)).willReturn(Optional.of(false));

        mockMvc.perform(get(statusUrl(workerId))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken))
                .andExpect(jsonPath("$.operatorAddress").value(newOperator))
                .andExpect(jsonPath("$.approved").value(false));
    }

    @Test
    @DisplayName("공급자 지갑이 없으면 PROVIDER_WALLET_MISSING — 체인에 묻지도 않는다")
    void providerWalletMissing() throws Exception {
        User noWallet = userRepository.save(User.createUser(
                "nowallet-" + UUID.randomUUID() + "@gshared.io", "encoded", "지갑없음"));
        String token = jwtProvider.createAccessToken(noWallet.getId(), noWallet.getRole());
        String id = workerService.register(noWallet.getId(),
                new RegisterWorkerRequest(OPERATOR, "0.1.0",
                        new RegisterWorkerRequest.SystemInfo("Windows 11", true),
                        new RegisterWorkerRequest.GpuInfo("RTX 3060", 12288),
                        new RegisterWorkerRequest.BenchmarkInfo(900.0, "transformer-bench-v1"))).workerId();

        mockMvc.perform(get(statusUrl(id))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.providerAddress").doesNotExist())
                .andExpect(jsonPath("$.operatorAddress").value(OPERATOR))
                .andExpect(jsonPath("$.approved").value(false))
                .andExpect(jsonPath("$.status").value("PROVIDER_WALLET_MISSING"));

        then(chain).should(never()).isOperator(any(), any());
    }

    @Test
    @DisplayName("노드가 답하지 않으면 UNVERIFIED — 승인 안 함(false)과 다르고, 오류(5xx)도 아니다")
    void chainUnreachable() throws Exception {
        given(chain.isOperator(anyString(), anyString())).willReturn(Optional.empty());

        mockMvc.perform(get(statusUrl(workerId))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.approved").value(false))
                .andExpect(jsonPath("$.status").value("UNVERIFIED"));
    }

    @Test
    @DisplayName("남의 워커는 404 — 존재 여부를 흘리지 않는다")
    void otherOwnersWorkerIsNotFound() throws Exception {
        User other = userRepository.save(User.createUser(
                "other-" + UUID.randomUUID() + "@gshared.io", "encoded", "남"));
        String otherToken = jwtProvider.createAccessToken(other.getId(), other.getRole());

        mockMvc.perform(get(statusUrl(workerId))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + otherToken))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("WORKER_NOT_FOUND"));
    }

    @Test
    @DisplayName("로그인 없이는 볼 수 없다 — 워커 토큰 경로(/operator)와 달리 사람이 부르는 곳이다")
    void requiresLogin() throws Exception {
        mockMvc.perform(get(statusUrl(workerId)))
                .andExpect(status().isUnauthorized());
    }

    // ── GET /api/workers/me — availability ───────────────────────

    @Test
    @DisplayName("등록 직후(OFFLINE)는 availability 도 OFFLINE — 체인에 묻지 않는다")
    void offlineWorkerIsOffline() throws Exception {
        mockMvc.perform(get(MINE_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].status").value("OFFLINE"))
                .andExpect(jsonPath("$[0].availability").value("OFFLINE"));

        then(chain).should(never()).isOperator(any(), any());
    }

    @Test
    @DisplayName("ONLINE 인데 승인이 없으면 OPERATOR_APPROVAL_REQUIRED — ONLINE != AVAILABLE")
    void onlineButNotApproved() throws Exception {
        goOnline();
        given(chain.isOperator(PROVIDER_WALLET, OPERATOR)).willReturn(Optional.of(false));

        mockMvc.perform(get(MINE_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken))
                .andExpect(jsonPath("$[0].status").value("ONLINE"))
                .andExpect(jsonPath("$[0].availability").value("OPERATOR_APPROVAL_REQUIRED"));
    }

    @Test
    @DisplayName("ONLINE + 승인 → AVAILABLE")
    void onlineAndApproved() throws Exception {
        goOnline();
        given(chain.isOperator(PROVIDER_WALLET, OPERATOR)).willReturn(Optional.of(true));

        mockMvc.perform(get(MINE_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken))
                .andExpect(jsonPath("$[0].availability").value("AVAILABLE"));
    }

    @Test
    @DisplayName("작업 중이면 BUSY — 승인됐어도 지금은 못 받는다, 체인에 묻지도 않는다")
    void runningIsBusy() throws Exception {
        Worker worker = workerRepository.findById(workerId).orElseThrow();
        worker.goOnline(LocalDateTime.now());
        worker.startJob(42L);
        workerRepository.flush();
        given(chain.isOperator(PROVIDER_WALLET, OPERATOR)).willReturn(Optional.of(true));

        mockMvc.perform(get(MINE_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken))
                .andExpect(jsonPath("$[0].status").value("RUNNING"))
                .andExpect(jsonPath("$[0].availability").value("BUSY"));

        then(chain).should(never()).isOperator(any(), any());
    }

    @Test
    @DisplayName("체인이 설정됐는데 답이 없으면 CHAIN_VERIFICATION_FAILED")
    void chainFailureIsVisible() throws Exception {
        goOnline();
        given(chain.isOperator(anyString(), anyString())).willReturn(Optional.empty());

        mockMvc.perform(get(MINE_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken))
                .andExpect(jsonPath("$[0].availability").value("CHAIN_VERIFICATION_FAILED"));
    }
}
