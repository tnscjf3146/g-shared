package com.gshared.back.job.controller;

import com.gshared.back.global.security.jwt.JwtProvider;
import com.gshared.back.job.domain.Job;
import com.gshared.back.job.domain.JobStatus;
import com.gshared.back.job.repository.JobRepository;
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

import java.time.LocalDateTime;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * Job 예치 확인(fund) API 통합 테스트.
 * CREATED → FUNDED 전이가 소유권·상태 조건을 지키는지 실제 필터 체인을 거쳐 확인한다.
 * 테스트 트랜잭션이 롤백되므로 DB 에는 흔적이 남지 않는다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional
class JobFundIntegrationTest {

    @Autowired
    private MockMvc mockMvc;
    @Autowired
    private UserRepository userRepository;
    @Autowired
    private JobRepository jobRepository;
    @Autowired
    private JwtProvider jwtProvider;

    @Autowired
    private com.gshared.back.dataset.repository.DatasetRepository datasetRepository;

    private User owner;
    private String ownerToken;
    private Long datasetId;   // jobs.dataset_id FK 만족용

    @BeforeEach
    void setUp() {
        owner = saveUser("fund-owner");
        ownerToken = jwtProvider.createAccessToken(owner.getId(), owner.getRole());
        datasetId = datasetRepository.save(com.gshared.back.dataset.domain.Dataset.create(
                owner, 52_428_800L, 10_000, 3_500_000L, 2_874_100L, 350.0, 1800, null, null)).getId();
    }

    private User saveUser(String prefix) {
        return userRepository.save(User.createUser(
                prefix + "-" + UUID.randomUUID() + "@gshared.io", "encoded", "펀드테스터"));
    }

    private Job saveJob(User jobOwner) {
        return jobRepository.save(Job.create(
                jobOwner, "qlora-sft-v1", "qwen2.5-7b", datasetId,
                "AUTO", 3, 8, 0.0002, 1024, 1500.0, "runtime-xgb-v1", null, 3000L));
    }

    private String fundUrl(Long jobId) {
        return "/api/jobs/" + jobId + "/fund";
    }

    @Test
    @DisplayName("CREATED 상태의 본인 Job 은 FUNDED 로 전이된다")
    void fund_created_job() throws Exception {
        Job job = saveJob(owner);

        mockMvc.perform(post(fundUrl(job.getId()))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken))
                .andExpect(status().isOk());

        assertThat(jobRepository.findById(job.getId()).orElseThrow().getStatus())
                .isEqualTo(JobStatus.FUNDED);
    }

    @Test
    @DisplayName("남의 Job 은 404 — 존재 여부조차 노출하지 않는다")
    void fund_others_job_returns_404() throws Exception {
        Job othersJob = saveJob(saveUser("other"));

        mockMvc.perform(post(fundUrl(othersJob.getId()))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken))
                .andExpect(status().isNotFound());

        assertThat(jobRepository.findById(othersJob.getId()).orElseThrow().getStatus())
                .isEqualTo(JobStatus.CREATED);   // 상태가 바뀌지 않아야 한다
    }

    @Test
    @DisplayName("이미 FUNDED 인 Job 재호출은 409")
    void fund_twice_returns_409() throws Exception {
        Job job = saveJob(owner);
        jobRepository.fundIfCreated(job.getId(), owner.getId());   // 1차 전이

        mockMvc.perform(post(fundUrl(job.getId()))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken))
                .andExpect(status().isConflict());
    }

    @Test
    @DisplayName("존재하지 않는 Job 은 404")
    void fund_missing_job_returns_404() throws Exception {
        mockMvc.perform(post(fundUrl(999_999L))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken))
                .andExpect(status().isNotFound());
    }

    @Test
    @DisplayName("토큰 없는 요청은 401")
    void fund_without_token_returns_401() throws Exception {
        Job job = saveJob(owner);

        mockMvc.perform(post(fundUrl(job.getId())))
                .andExpect(status().isUnauthorized());
    }

    @Test
    @DisplayName("취소된 Job 은 409 — 취소를 되살리지 않는다")
    void fund_cancelled_job_returns_409() throws Exception {
        Job job = saveJob(owner);
        jobRepository.cancelIfCancellable(job.getId(), owner.getId(), LocalDateTime.now());

        mockMvc.perform(post(fundUrl(job.getId()))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken))
                .andExpect(status().isConflict());

        assertThat(jobRepository.findById(job.getId()).orElseThrow().getStatus())
                .isEqualTo(JobStatus.CANCELLED);   // CANCELLED 유지
    }
}
