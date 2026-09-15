package com.gshared.back.worker.controller;

import com.gshared.back.global.security.jwt.JwtProvider;
import com.gshared.back.user.domain.User;
import com.gshared.back.user.repository.UserRepository;
import com.gshared.back.worker.domain.Worker;
import com.gshared.back.worker.repository.WorkerRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.HttpHeaders;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDateTime;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 네트워크 워커 현황 (2026-09-15 프론트 API 요청 ④). 다른 테스트가 남긴 워커가 같은 DB 에 있을 수 있어
 * 절대값이 아니라 "내가 추가한 만큼 늘었는가"로 확인한다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional
class NetworkStatsIntegrationTest {

    private static final String STATS_URL = "/api/network/stats";

    @Autowired
    private MockMvc mockMvc;
    @Autowired
    private ObjectMapper objectMapper;
    @Autowired
    private UserRepository userRepository;
    @Autowired
    private WorkerRepository workerRepository;
    @Autowired
    private JwtProvider jwtProvider;

    private User owner;
    private String token;

    @BeforeEach
    void setUp() {
        owner = userRepository.save(User.createUser(
                "stats-" + UUID.randomUUID() + "@gshared.io", "encoded", "지표"));
        token = jwtProvider.createAccessToken(owner.getId(), owner.getRole());
    }

    private Worker saveWorker() {
        return workerRepository.save(Worker.register(
                "worker-stats-" + UUID.randomUUID(), owner, "hash-" + UUID.randomUUID(),
                "0x" + "b".repeat(40), "0.1.0", "Windows 11", true,
                "RTX 4090", 24_576, 1800.0, "transformer-bench-v1"));
    }

    private JsonNode stats() throws Exception {
        MvcResult result = mockMvc.perform(get(STATS_URL).header(HttpHeaders.AUTHORIZATION, "Bearer " + token))
                .andExpect(status().isOk())
                .andReturn();
        return objectMapper.readTree(result.getResponse().getContentAsString());
    }

    @Test
    @DisplayName("onlineWorkers 는 ONLINE·RUNNING 만, totalWorkers 는 OFFLINE 포함 전체")
    void countsByStatus() throws Exception {
        JsonNode before = stats();
        long online0 = before.path("onlineWorkers").asLong();
        long total0 = before.path("totalWorkers").asLong();

        saveWorker();                                    // 등록 직후 OFFLINE — total 만 +1
        saveWorker().goOnline(LocalDateTime.now());      // ONLINE
        Worker running = saveWorker();
        running.goOnline(LocalDateTime.now());
        running.startJob(1L);                            // RUNNING
        workerRepository.flush();

        JsonNode after = stats();
        assertThat(after.path("onlineWorkers").asLong()).isEqualTo(online0 + 2);
        assertThat(after.path("totalWorkers").asLong()).isEqualTo(total0 + 3);
    }

    @Test
    @DisplayName("토큰 없으면 401")
    void requiresLogin() throws Exception {
        mockMvc.perform(get(STATS_URL)).andExpect(status().isUnauthorized());
    }
}
