package com.gshared.back.workload.controller;

import com.gshared.back.global.security.jwt.JwtProvider;
import com.gshared.back.user.domain.User;
import com.gshared.back.user.repository.UserRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.HttpHeaders;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.annotation.Transactional;

import java.util.UUID;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 메뉴판 조회 두 곳. 화면 1·2단계가 이 응답으로 선택지를 그린다.
 *
 * <p>필드 이름을 하나하나 확인하는 이유: 화면이 응답을 스키마로 검증해서, 이름이 하나만
 * 달라도 목록이 통째로 비어 보인다. 값이 맞는지보다 계약이 맞는지가 여기서는 더 중요하다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional
class CatalogIntegrationTest {

    @Autowired
    private MockMvc mockMvc;
    @Autowired
    private UserRepository userRepository;
    @Autowired
    private JwtProvider jwtProvider;

    private String accessToken;

    @BeforeEach
    void setUp() {
        User user = userRepository.save(User.createUser(
                "catalog-" + UUID.randomUUID() + "@gshared.io", "encoded", "카탈로그"));
        accessToken = jwtProvider.createAccessToken(user.getId(), user.getRole());
    }

    @Test
    @DisplayName("워크로드 목록은 유형과 설명까지 담아 내려온다")
    void getWorkloads() throws Exception {
        mockMvc.perform(get("/api/workloads")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].id").isNumber())
                .andExpect(jsonPath("$[0].kind").value("TRAIN_LORA"))
                .andExpect(jsonPath("$[0].name").isString())
                .andExpect(jsonPath("$[0].description").isString());
    }

    @Test
    @DisplayName("베이스 모델 목록은 파라미터 수를 사람이 읽는 문자열로 준다")
    void getBaseModels() throws Exception {
        mockMvc.perform(get("/api/base-models")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].id").isNumber())
                .andExpect(jsonPath("$[0].name").isString())
                // 600000000 이 아니라 "600M" — 자릿수 세는 일을 화면에 넘기지 않는다
                .andExpect(jsonPath("$[0].params").value("600M"))
                .andExpect(jsonPath("$[0].workloadIds").isArray())
                .andExpect(jsonPath("$[0].workloadIds[0]").isNumber())
                // Runtime 이 학습할 수 없는 모델(qwen2.5-7b, V14 에서 enabled=false)은 메뉴판에 나오지 않는다
                .andExpect(jsonPath("$[?(@.name == 'Qwen 2.5 7B')]").isEmpty());
    }

    @Test
    @DisplayName("로그인하지 않으면 메뉴판도 볼 수 없다")
    void requiresLogin() throws Exception {
        mockMvc.perform(get("/api/workloads"))
                .andExpect(status().isUnauthorized());
    }

    @Test
    @DisplayName("워커가 하나도 없으면 GPU 등급은 빈 목록이다 — 없는 것을 감추지 않는다")
    void gpusAreEmptyWithoutWorkers() throws Exception {
        mockMvc.perform(get("/api/gpus")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$").isArray())
                .andExpect(jsonPath("$.length()").value(0));
    }
}
