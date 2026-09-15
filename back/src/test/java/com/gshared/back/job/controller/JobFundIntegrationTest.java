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
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
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

    @Autowired
    private com.gshared.back.workload.repository.WorkloadRepository workloadRepository;
    @Autowired
    private com.gshared.back.workload.repository.BaseModelRepository baseModelRepository;

    private User owner;
    private String ownerToken;
    private Long datasetId;   // jobs.dataset_id FK 만족용
    private Long workloadId;  // V8 seed 카탈로그 정수 PK (slug 로 조회)
    private Long baseModelId;

    @BeforeEach
    void setUp() {
        owner = saveUser("fund-owner");
        ownerToken = jwtProvider.createAccessToken(owner.getId(), owner.getRole());
        datasetId = datasetRepository.save(com.gshared.back.dataset.domain.Dataset.create(
                owner, 52_428_800L, 10_000, 3_500_000L, 2_874_100L, 350.0, 1800, null, null)).getId();
        workloadId = workloadRepository.findBySlug("qlora-sft-v1").orElseThrow().getId();
        baseModelId = baseModelRepository.findBySlug("qwen3-0.6b").orElseThrow().getId();
    }

    private User saveUser(String prefix) {
        return userRepository.save(User.createUser(
                prefix + "-" + UUID.randomUUID() + "@gshared.io", "encoded", "펀드테스터"));
    }

    private Job saveJob(User jobOwner) {
        return jobRepository.save(Job.create(
                jobOwner, workloadId, baseModelId, datasetId,
                "AUTO", 3, 8, 0.0002, 1024, 1500.0, "runtime-xgb-v1", null, 3000L));
    }

    /**
     * 이 테스트는 체인 없이 돈다(escrow.* 비어 있음 · verification-required=false). 그 모드에서는 대조할 체인이
     * 없으므로 txHash 와 함께 받은 escrowJobId 를 그대로 기록한다 — JOB_ASSIGN 에 실을 온체인 jobId 가 필요하다.
     * 체인이 있을 때의 영수증 대조는 EscrowChainClientLiveTest · JobFundVerificationTest 가 본다.
     */
    private static final String TX = "0x" + "ab".repeat(32);
    private static final String OTHER_TX = "0x" + "cd".repeat(32);
    private static final String ESCROW_BODY = "{\"txHash\":\"" + TX + "\",\"escrowJobId\":\"17\"}";

    private String fundUrl(Long jobId) {
        return "/api/jobs/" + jobId + "/fund";
    }

    @Test
    @DisplayName("CREATED 상태의 본인 Job 은 FUNDED 로 전이된다")
    void fund_created_job() throws Exception {
        Job job = saveJob(owner);

        mockMvc.perform(post(fundUrl(job.getId()))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken)
                        .contentType(org.springframework.http.MediaType.APPLICATION_JSON)
                        .content(ESCROW_BODY))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("FUNDED"))
                .andExpect(jsonPath("$.escrowJobId").value("17"));

        Job funded = jobRepository.findById(job.getId()).orElseThrow();
        assertThat(funded.getStatus()).isEqualTo(JobStatus.FUNDED);
        assertThat(funded.getEscrowJobId()).isEqualTo("17");   // 온체인 jobId 기록
        assertThat(funded.getFundTxHash()).isEqualTo(TX);
        assertThat(funded.getEscrowChainId()).isEqualTo(31221L);
    }

    @Test
    @DisplayName("같은 txHash 재전송은 같은 답(200 FUNDED) — 멱등")
    void fund_same_tx_twice_is_idempotent() throws Exception {
        Job job = saveJob(owner);
        fund(job.getId(), ESCROW_BODY).andExpect(status().isOk());

        fund(job.getId(), ESCROW_BODY)
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("FUNDED"))
                .andExpect(jsonPath("$.txHash").value(TX));
    }

    @Test
    @DisplayName("다른 Job 이 이미 쓴 txHash 는 409 — 한 예치로 두 작업을 돌릴 수 없다")
    void fund_with_reused_tx_returns_409() throws Exception {
        Job first = saveJob(owner);
        Job second = saveJob(owner);
        fund(first.getId(), ESCROW_BODY).andExpect(status().isOk());

        fund(second.getId(), "{\"txHash\":\"" + TX + "\",\"escrowJobId\":\"18\"}")
                .andExpect(status().isConflict());
        assertThat(jobRepository.findById(second.getId()).orElseThrow().getStatus()).isEqualTo(JobStatus.CREATED);
    }

    @Test
    @DisplayName("txHash 가 없거나 형식이 틀리면 400")
    void fund_without_tx_hash() throws Exception {
        Job job = saveJob(owner);

        fund(job.getId(), "{\"escrowJobId\":\"17\"}").andExpect(status().isBadRequest());
        fund(job.getId(), "{\"txHash\":\"0x1234\",\"escrowJobId\":\"17\"}").andExpect(status().isBadRequest());
        assertThat(jobRepository.findById(job.getId()).orElseThrow().getStatus()).isEqualTo(JobStatus.CREATED);
    }

    private org.springframework.test.web.servlet.ResultActions fund(Long jobId, String body) throws Exception {
        return mockMvc.perform(post(fundUrl(jobId))
                .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken)
                .contentType(org.springframework.http.MediaType.APPLICATION_JSON)
                .content(body));
    }

    @Test
    @DisplayName("체인 없는 모드에서 escrowJobId 가 없으면 400 — JOB_ASSIGN 에 실을 온체인 jobId 없이는 FUNDED 로 두지 않는다")
    void fund_without_escrow_job_id() throws Exception {
        Job job = saveJob(owner);

        mockMvc.perform(post(fundUrl(job.getId()))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken))
                .andExpect(status().isBadRequest());
        fund(job.getId(), "{\"txHash\":\"" + TX + "\"}").andExpect(status().isBadRequest());

        assertThat(jobRepository.findById(job.getId()).orElseThrow().getStatus()).isEqualTo(JobStatus.CREATED);
    }

    @Test
    @DisplayName("escrowJobId 가 정수 문자열이 아니면 400 — uint256 은 숫자 문자열로만 받는다")
    void fund_with_invalid_escrow_job_id() throws Exception {
        Job job = saveJob(owner);

        mockMvc.perform(post(fundUrl(job.getId()))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken)
                        .contentType(org.springframework.http.MediaType.APPLICATION_JSON)
                        .content("{\"txHash\":\"" + TX + "\",\"escrowJobId\":\"0xabc\"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    @DisplayName("남의 Job 은 404 — 존재 여부조차 노출하지 않는다")
    void fund_others_job_returns_404() throws Exception {
        Job othersJob = saveJob(saveUser("other"));

        mockMvc.perform(post(fundUrl(othersJob.getId()))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken)
                        .contentType(org.springframework.http.MediaType.APPLICATION_JSON)
                        .content(ESCROW_BODY))
                .andExpect(status().isNotFound());

        assertThat(jobRepository.findById(othersJob.getId()).orElseThrow().getStatus())
                .isEqualTo(JobStatus.CREATED);   // 상태가 바뀌지 않아야 한다
    }

    @Test
    @DisplayName("이미 다른 txHash 로 FUNDED 인 Job 재호출은 409")
    void fund_twice_returns_409() throws Exception {
        Job job = saveJob(owner);
        jobRepository.fundIfCreated(job.getId(), 31221L, null, "16", OTHER_TX);   // 1차 전이

        mockMvc.perform(post(fundUrl(job.getId()))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken)
                        .contentType(org.springframework.http.MediaType.APPLICATION_JSON)
                        .content(ESCROW_BODY))
                .andExpect(status().isConflict());
    }

    @Test
    @DisplayName("존재하지 않는 Job 은 404")
    void fund_missing_job_returns_404() throws Exception {
        mockMvc.perform(post(fundUrl(999_999L))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken)
                        .contentType(org.springframework.http.MediaType.APPLICATION_JSON)
                        .content(ESCROW_BODY))
                .andExpect(status().isNotFound());
    }

    @Test
    @DisplayName("토큰 없는 요청은 401")
    void fund_without_token_returns_401() throws Exception {
        Job job = saveJob(owner);

        mockMvc.perform(post(fundUrl(job.getId()))
                        .contentType(org.springframework.http.MediaType.APPLICATION_JSON)
                        .content(ESCROW_BODY))
                .andExpect(status().isUnauthorized());
    }

    @Test
    @DisplayName("취소된 Job 은 409 — 취소를 되살리지 않는다")
    void fund_cancelled_job_returns_409() throws Exception {
        Job job = saveJob(owner);
        jobRepository.cancelIfUnassigned(job.getId(), owner.getId(), LocalDateTime.now());

        mockMvc.perform(post(fundUrl(job.getId()))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken)
                        .contentType(org.springframework.http.MediaType.APPLICATION_JSON)
                        .content(ESCROW_BODY))
                .andExpect(status().isConflict());

        assertThat(jobRepository.findById(job.getId()).orElseThrow().getStatus())
                .isEqualTo(JobStatus.CANCELLED);   // CANCELLED 유지
    }

    // ── GET /api/jobs/{jobId} — 예치 입력값 (escrow-terms 창구 대신 상세 응답이 싣는다) ──

    @Test
    @DisplayName("상세 응답은 CreateJobResponse 와 같은 예치 입력값을 싣는다 — 새로고침 뒤에도 예치를 이어갈 수 있게")
    void detail_carries_escrow_terms() throws Exception {
        owner.linkWallet("0x" + "a".repeat(40));
        Job job = saveJob(owner);

        mockMvc.perform(get("/api/jobs/" + job.getId())
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.externalRef").value(job.getExternalRef()))
                .andExpect(jsonPath("$.priceWei").value("3000000000000000000000"))   // 3000 TOKEN → wei, 10진 문자열
                .andExpect(jsonPath("$.clientWallet").value("0x" + "a".repeat(40)))
                .andExpect(jsonPath("$.chainId").value(31221))
                .andExpect(jsonPath("$.tokenAddress").isString())
                .andExpect(jsonPath("$.escrowAddress").isEmpty())   // 이 테스트는 체인 미설정 → null
                .andExpect(jsonPath("$.escrowJobId").isEmpty());    // CREATED — 예치 전

        mockMvc.perform(post(fundUrl(job.getId()))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken)
                        .contentType(org.springframework.http.MediaType.APPLICATION_JSON)
                        .content(ESCROW_BODY))
                .andExpect(status().isOk());

        mockMvc.perform(get("/api/jobs/" + job.getId())
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.escrowJobId").value("17"));   // FUNDED 뒤 취소 화면이 쓰는 온체인 번호
    }
}
