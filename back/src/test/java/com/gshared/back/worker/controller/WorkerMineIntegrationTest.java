package com.gshared.back.worker.controller;

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
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 공급자가 자기 PC 를 보고 지우는 두 창구.
 *
 * <p>워커 프로그램이 부르는 {@code /authenticate}·{@code /gpu} 와 달리 여기는 사람이
 * 로그인해서 부르는 곳이라 인증 수단이 로그인 JWT 다. 워커 토큰으로는 들어올 수 없어야 한다 —
 * 토큰이 새어 나갔을 때 남의 등록을 지울 수 있으면 안 된다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional
class WorkerMineIntegrationTest {

    private static final String MINE_URL = "/api/workers/me";

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

    private User owner;
    private String ownerToken;
    private String workerId;

    @BeforeEach
    void setUp() {
        owner = userRepository.save(User.createUser(
                "mine-" + UUID.randomUUID() + "@gshared.io", "encoded", "워커주인"));
        ownerToken = jwtProvider.createAccessToken(owner.getId(), owner.getRole());

        RegisterWorkerResponse registered = workerService.register(owner.getId(),
                new RegisterWorkerRequest("0x" + "b".repeat(40), "0.1.0",
                        new RegisterWorkerRequest.SystemInfo("Windows 11", true),
                        new RegisterWorkerRequest.GpuInfo("RTX 4090", 24576),
                        new RegisterWorkerRequest.BenchmarkInfo(1800.0, "transformer-bench-v1")));
        workerId = registered.workerId();
    }

    private String workerUrl(String id) {
        return "/api/workers/" + id;
    }

    // ── GET /api/workers/me ─────────────────────────────────────

    @Test
    @DisplayName("내 워커 목록에는 등록한 PC 의 스펙과 상태가 담긴다")
    void getMyWorkers() throws Exception {
        mockMvc.perform(get(MINE_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(1))
                .andExpect(jsonPath("$[0].workerId").value(workerId))
                // 규격 v1 등록에는 deviceName 이 없다 — 화면이 문자열을 기대하므로 null 이 아니라 빈 값
                .andExpect(jsonPath("$[0].deviceName").value(""))
                .andExpect(jsonPath("$[0].osVersion").value("Windows 11"))
                .andExpect(jsonPath("$[0].wslAvailable").value(true))
                .andExpect(jsonPath("$[0].clientVersion").value("0.1.0"))
                .andExpect(jsonPath("$[0].gpuModel").value("RTX 4090"))
                .andExpect(jsonPath("$[0].vramMb").value(24576))
                .andExpect(jsonPath("$[0].benchmarkVersion").value("transformer-bench-v1"))
                .andExpect(jsonPath("$[0].operatorAddress").value("0x" + "b".repeat(40)))
                // 등록 직후엔 아직 접속 전이라 OFFLINE 이다
                .andExpect(jsonPath("$[0].status").value("OFFLINE"))
                .andExpect(jsonPath("$[0].currentJobId").doesNotExist());
    }

    @Test
    @DisplayName("워커 토큰은 절대 목록에 실리지 않는다 — 등록 때 1회 노출이라는 전제가 깨진다")
    void neverExposesWorkerToken() throws Exception {
        mockMvc.perform(get(MINE_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].workerToken").doesNotExist());
    }

    @Test
    @DisplayName("지갑을 연결하지 않았으면 walletLinked 가 false — 왜 후보에 안 뜨는지 본인은 알아야 한다")
    void tellsWalletIsMissing() throws Exception {
        mockMvc.perform(get(MINE_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].walletLinked").value(false));
    }

    @Test
    @DisplayName("남의 워커는 내 목록에 섞이지 않는다")
    void showsOnlyMine() throws Exception {
        User other = userRepository.save(User.createUser(
                "other-" + UUID.randomUUID() + "@gshared.io", "encoded", "남"));
        workerService.register(other.getId(),
                new RegisterWorkerRequest("0x" + "c".repeat(40), "0.1.0",
                        new RegisterWorkerRequest.SystemInfo("Windows 11", true),
                        new RegisterWorkerRequest.GpuInfo("RTX 3060", 12288),
                        new RegisterWorkerRequest.BenchmarkInfo(900.0, "transformer-bench-v1")));

        mockMvc.perform(get(MINE_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(1))
                .andExpect(jsonPath("$[0].workerId").value(workerId));
    }

    @Test
    @DisplayName("한 대도 없으면 빈 목록 — 오류가 아니라 아직 안 붙인 정상 상태다")
    void emptyWhenNoWorkers() throws Exception {
        User fresh = userRepository.save(User.createUser(
                "fresh-" + UUID.randomUUID() + "@gshared.io", "encoded", "신규"));

        mockMvc.perform(get(MINE_URL)
                        .header(HttpHeaders.AUTHORIZATION,
                                "Bearer " + jwtProvider.createAccessToken(fresh.getId(), fresh.getRole())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(0));
    }

    @Test
    @DisplayName("로그인 없이는 볼 수 없다")
    void mineRequiresLogin() throws Exception {
        mockMvc.perform(get(MINE_URL))
                .andExpect(status().isUnauthorized());
    }

    // ── DELETE /api/workers/{workerId} ──────────────────────────

    @Test
    @DisplayName("등록 해제하면 204 이고 행이 사라진다")
    void deleteWorker() throws Exception {
        mockMvc.perform(delete(workerUrl(workerId))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken))
                .andExpect(status().isNoContent());

        assertThat(workerRepository.findById(workerId)).isEmpty();
    }

    @Test
    @DisplayName("남의 워커는 지울 수 없다 — 존재 여부도 흘리지 않으려 404 다")
    void cannotDeleteOthers() throws Exception {
        User other = userRepository.save(User.createUser(
                "thief-" + UUID.randomUUID() + "@gshared.io", "encoded", "남"));
        String otherToken = jwtProvider.createAccessToken(other.getId(), other.getRole());

        mockMvc.perform(delete(workerUrl(workerId))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + otherToken))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("WORKER_NOT_FOUND"));

        assertThat(workerRepository.findById(workerId)).isPresent();
    }

    @Test
    @DisplayName("없는 워커를 지우면 404")
    void deleteUnknown() throws Exception {
        mockMvc.perform(delete(workerUrl("worker-" + UUID.randomUUID()))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("WORKER_NOT_FOUND"));
    }

    @Test
    @DisplayName("작업 중인 워커는 지울 수 없다 — 지우면 그 Job 이 결과 받을 상대를 잃는다")
    void cannotDeleteBusyWorker() throws Exception {
        Worker worker = workerRepository.findById(workerId).orElseThrow();
        worker.goOnline(LocalDateTime.now());
        worker.startJob(1L);
        workerRepository.flush();

        mockMvc.perform(delete(workerUrl(workerId))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("WORKER_BUSY"));

        assertThat(workerRepository.findById(workerId)).isPresent();
    }
}
