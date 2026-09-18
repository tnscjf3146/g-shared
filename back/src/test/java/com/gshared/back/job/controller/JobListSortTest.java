package com.gshared.back.job.controller;

import com.gshared.back.dataset.domain.Dataset;
import com.gshared.back.dataset.repository.DatasetRepository;
import com.gshared.back.global.security.jwt.JwtProvider;
import com.gshared.back.job.domain.Job;
import com.gshared.back.job.repository.JobRepository;
import com.gshared.back.user.domain.User;
import com.gshared.back.user.repository.UserRepository;
import com.gshared.back.workload.repository.BaseModelRepository;
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

import static org.hamcrest.Matchers.greaterThan;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 목록 정렬 계약 검증 — 프런트가 {@code ?sort=id,asc} 로 서버 정렬을 덮어쓸 수 있는가.
 * {@code @PageableDefault} 는 기본값일 뿐이라는 가정을 코드로 못 박는다 (대시보드 '오래된순'이 여기 의존).
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional
class JobListSortTest {

    @Autowired
    private MockMvc mockMvc;
    @Autowired
    private UserRepository userRepository;
    @Autowired
    private DatasetRepository datasetRepository;
    @Autowired
    private JobRepository jobRepository;
    @Autowired
    private BaseModelRepository baseModelRepository;
    @Autowired
    private JwtProvider jwtProvider;

    private String token;
    private Long firstId;
    private Long secondId;

    @BeforeEach
    void setUp() {
        User owner = userRepository.save(User.createUser(
                "sort-" + UUID.randomUUID() + "@gshared.io", "encoded", "정렬테스터"));
        token = jwtProvider.createAccessToken(owner.getId(), owner.getRole());
        Long datasetId = datasetRepository.save(Dataset.create(
                owner, 52_428_800L, 10_000, 2_500_000L, 1_800_000L, 250.0, 1024, null, null)).getId();
        var model = baseModelRepository.findAll().get(0);
        firstId = saveJob(owner, model.getWorkload().getId(), model.getId(), datasetId);
        secondId = saveJob(owner, model.getWorkload().getId(), model.getId(), datasetId);
    }

    private Long saveJob(User owner, Long workloadId, Long modelId, Long datasetId) {
        return jobRepository.save(Job.create(owner, workloadId, modelId, datasetId,
                "AUTO", 1, 16, 0.0002, 512, 100.0, "runtime-xgb-v1", null, 9L)).getId();
    }

    @Test
    @DisplayName("기본은 최신순(id desc) — 나중 만든 Job 이 먼저")
    void defaultNewestFirst() throws Exception {
        mockMvc.perform(get("/api/jobs?page=0&size=50")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.content[0].jobId").value(secondId))
                .andExpect(jsonPath("$.content[1].jobId").value(firstId));
    }

    @Test
    @DisplayName("?sort=id,asc 가 기본 정렬을 덮어쓴다 — 먼저 만든 Job 이 먼저 (대시보드 '오래된순' 계약)")
    void sortParamOverridesDefault() throws Exception {
        mockMvc.perform(get("/api/jobs?page=0&size=50&sort=id,asc")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.content[0].jobId").value(firstId))
                .andExpect(jsonPath("$.content[1].jobId").value(secondId))
                .andExpect(jsonPath("$.content.length()").value(greaterThan(1)));
    }
}
