package com.gshared.back.worker.controller;

import com.gshared.back.user.domain.User;
import com.gshared.back.user.repository.UserRepository;
import com.gshared.back.worker.dto.request.AuthenticateWorkerRequest;
import com.gshared.back.worker.dto.request.RegisterWorkerRequest;
import com.gshared.back.worker.dto.request.UpdateWorkerGpuRequest;
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
 * 사람 없이 워커 프로그램이 부르는 두 창구를 보안 필터까지 거쳐 확인한다.
 * 로그인 JWT 가 아니라 워커 토큰으로 인증되는지가 핵심이다.
 *
 * <p>404 응답의 code 가 WORKER_NOT_FOUND 인지를 매번 확인하는 이유:
 * 워커 클라이언트는 그 문자열을 보고 "내 등록이 사라졌으니 재등록하자"로 넘어간다.
 * 코드가 달라지면 워커는 재등록하지 못하고 그대로 멈춘다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional
class WorkerAuthIntegrationTest {

    private static final String AUTHENTICATE_URL = "/api/workers/authenticate";

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

    private String workerId;
    private String workerToken;

    @BeforeEach
    void setUp() {
        User owner = userRepository.save(User.createUser(
                "worker-" + UUID.randomUUID() + "@gshared.io", "encoded", "워커주인"));

        // 원문 토큰은 등록 응답에서만 나오므로 서비스로 등록해 받아둔다 (DB엔 해시만 남는다)
        RegisterWorkerResponse registered = workerService.register(owner.getId(),
                new RegisterWorkerRequest("테스트PC", "Windows 11", "RTX 4090",
                        24576, "561.09", 1800.0));
        workerId = registered.workerId();
        workerToken = registered.workerToken();
    }

    private String gpuUrl(String id) {
        return "/api/workers/" + id + "/gpu";
    }

    // ── POST /api/workers/authenticate ──────────────────────────

    @Test
    @DisplayName("워커 토큰으로 확인하면 서버가 인정한 workerId 를 돌려준다")
    void authenticate() throws Exception {
        mockMvc.perform(post(AUTHENTICATE_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + workerToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(new AuthenticateWorkerRequest(workerId))))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.workerId").value(workerId));
    }

    @Test
    @DisplayName("등록되지 않은 토큰이면 404 WORKER_NOT_FOUND — 워커는 이걸 보고 재등록한다")
    void authenticateWithUnknownToken() throws Exception {
        mockMvc.perform(post(AUTHENTICATE_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + UUID.randomUUID())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(new AuthenticateWorkerRequest(workerId))))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("WORKER_NOT_FOUND"));
    }

    @Test
    @DisplayName("토큰은 유효해도 다른 워커를 주장하면 404 WORKER_NOT_FOUND")
    void authenticateWithMismatchedWorkerId() throws Exception {
        mockMvc.perform(post(AUTHENTICATE_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + workerToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(
                                new AuthenticateWorkerRequest("worker-" + UUID.randomUUID()))))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("WORKER_NOT_FOUND"));
    }

    @Test
    @DisplayName("Authorization 헤더가 없으면 401 — 인증 시도 자체가 없었던 것이라 재등록 대상이 아니다")
    void authenticateWithoutHeader() throws Exception {
        mockMvc.perform(post(AUTHENTICATE_URL)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(new AuthenticateWorkerRequest(workerId))))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("INVALID_WORKER_TOKEN"));
    }

    // ── PUT /api/workers/{workerId}/gpu ─────────────────────────

    @Test
    @DisplayName("GPU 정보를 갱신하면 204 와 함께 저장된 스펙이 바뀐다")
    void updateGpu() throws Exception {
        mockMvc.perform(put(gpuUrl(workerId))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + workerToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(new UpdateWorkerGpuRequest(
                                "RTX 5090", 32768, "580.00", 2600.5))))
                .andExpect(status().isNoContent());

        assertThat(workerRepository.findById(workerId)).hasValueSatisfying(worker -> {
            assertThat(worker.getGpuModel()).isEqualTo("RTX 5090");
            assertThat(worker.getVramMb()).isEqualTo(32768);
            assertThat(worker.getDriverVersion()).isEqualTo("580.00");
            assertThat(worker.getBenchmarkTokensPerSec()).isEqualTo(2600.5);
        });
    }

    @Test
    @DisplayName("남의 워커 GPU 정보는 갱신할 수 없다")
    void updateGpuOfAnotherWorker() throws Exception {
        mockMvc.perform(put(gpuUrl("worker-" + UUID.randomUUID()))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + workerToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(new UpdateWorkerGpuRequest(
                                "RTX 5090", 32768, "580.00", 2600.5))))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("WORKER_NOT_FOUND"));

        // 내 워커 스펙도 그대로여야 한다
        assertThat(workerRepository.findById(workerId))
                .hasValueSatisfying(worker -> assertThat(worker.getGpuModel()).isEqualTo("RTX 4090"));
    }

    @Test
    @DisplayName("벤치마크 값이 0 이면 400 — 스케줄러가 빈 성능치를 그대로 믿게 두지 않는다")
    void updateGpuWithInvalidBenchmark() throws Exception {
        mockMvc.perform(put(gpuUrl(workerId))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + workerToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(new UpdateWorkerGpuRequest(
                                "RTX 5090", 32768, "580.00", 0.0))))
                .andExpect(status().isBadRequest());
    }
}
