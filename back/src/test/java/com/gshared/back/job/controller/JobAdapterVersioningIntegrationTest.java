package com.gshared.back.job.controller;

import com.gshared.back.dataset.domain.Dataset;
import com.gshared.back.dataset.repository.DatasetRepository;
import com.gshared.back.global.security.jwt.JwtProvider;
import com.gshared.back.job.domain.Job;
import com.gshared.back.job.domain.JobStatus;
import com.gshared.back.job.repository.JobRepository;
import com.gshared.back.user.domain.User;
import com.gshared.back.user.repository.UserRepository;
import com.gshared.back.worker.dto.request.RegisterWorkerRequest;
import com.gshared.back.worker.dto.response.RegisterWorkerResponse;
import com.gshared.back.worker.service.WorkerService;
import com.gshared.back.workload.domain.BaseModel;
import com.gshared.back.workload.repository.BaseModelRepository;
import jakarta.persistence.EntityManager;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 어댑터의 이름·버전·계보 (frontend job.md §11): 한 번 학습한 어댑터에서 이어서 학습하면 버전이 올라가고(v1 → v2 → v3),
 * 내려받을 이름은 나중에도 바꿀 수 있다.
 *
 * <p>여기서 확인하는 것은 <b>서버가 정한다</b>는 점이다 — 버전은 클라이언트가 못 정하고,
 * 부모는 "내 것 · 완료됨 · 결과물 있음"이어야 하며, 이어서 학습은 올릴 파일이 없어도 예치가 열린다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional
@TestPropertySource(properties = {
        "storage.root=${java.io.tmpdir}/gshared-test-storage",
        "storage.public-base-url=http://localhost:8080"
})
class JobAdapterVersioningIntegrationTest {

    private static final String RESULT_SHA = "1a".repeat(32);

    @Autowired
    private MockMvc mockMvc;
    @Autowired
    private UserRepository userRepository;
    @Autowired
    private DatasetRepository datasetRepository;
    @Autowired
    private BaseModelRepository baseModelRepository;
    @Autowired
    private JobRepository jobRepository;
    @Autowired
    private WorkerService workerService;
    @Autowired
    private JwtProvider jwtProvider;
    @Autowired
    private EntityManager em;

    private User owner;
    private String ownerToken;
    private Long workloadId;
    private Long baseModelId;

    @BeforeEach
    void setUp() {
        owner = userRepository.save(User.createUser(
                "lineage-" + UUID.randomUUID() + "@gshared.io", "encoded", "계보테스터"));
        ownerToken = jwtProvider.createAccessToken(owner.getId(), owner.getRole());
        BaseModel baseModel = baseModelRepository.findBySlug("qwen3-0.6b").orElseThrow();
        workloadId = baseModel.getWorkload().getId();
        baseModelId = baseModel.getId();
    }

    /** Job 하나. 어댑터 주문은 인자로 직접 준다 — 여기서는 주문 API 가 아니라 계보 규칙이 관심사다. */
    private Job job(User user, String adapterName, int version, Long parentJobId, boolean required) {
        Dataset dataset = datasetRepository.save(Dataset.create(
                user, 100L, 2, 8L, 8L, 4.0, 4, "0x" + "3c".repeat(32), null));
        Job job = jobRepository.save(Job.create(user, workloadId, baseModelId, dataset.getId(),
                "AUTO", 1, 16, 0.0002, 512, 985.882, "runtime-xgb-v1", null, 9L));
        job.assignAdapterOrder(required, adapterName, version, parentJobId);
        job.recordDatasetUpload("jobs/" + job.getId() + "/input/dataset.jsonl", 100L, "3c".repeat(32),
                LocalDateTime.now());
        return job;
    }

    /** 결과물까지 올라온 완료 Job — 이어서 학습의 출발점이 될 수 있는 상태. */
    private Job completedJob(User user, String adapterName, int version) {
        Job job = job(user, adapterName, version, null, false);
        job.recordResultUpload("jobs/" + job.getId() + "/result.tar", 4096L, RESULT_SHA);
        ReflectionTestUtils.setField(job, "status", JobStatus.COMPLETED);
        em.flush();
        return job;
    }

    private ResultActions rename(Long jobId, String name, String token) throws Exception {
        return mockMvc.perform(patch("/api/jobs/" + jobId + "/adapter-name")
                .header(HttpHeaders.AUTHORIZATION, "Bearer " + token)
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"adapterName\":\"" + name + "\"}"));
    }

    /** 배정받을 워커 하나 — execution-context 를 부르려면 실제 워커 토큰이 필요하다. */
    private RegisterWorkerResponse worker() {
        User provider = userRepository.save(User.createUser(
                "provider-" + UUID.randomUUID() + "@gshared.io", "encoded", "공급자"));
        provider.linkWallet("0x" + "b".repeat(40));
        return workerService.register(provider.getId(), new RegisterWorkerRequest(
                "0x" + "c".repeat(40), "0.1.0",
                new RegisterWorkerRequest.SystemInfo("Windows 11", true),
                new RegisterWorkerRequest.GpuInfo("RTX 4090", 24576),
                new RegisterWorkerRequest.BenchmarkInfo(42000.0, null, "transformer-bench-v1")));
    }

    private ResultActions detail(Long jobId) throws Exception {
        return mockMvc.perform(get("/api/jobs/" + jobId)
                .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken));
    }

    // ── 상세·목록 응답 ────────────────────────────────────────

    @Test
    @DisplayName("상세와 목록이 어댑터 이름·버전·계보를 싣는다")
    void exposesLineage() throws Exception {
        Job parent = completedJob(owner, "cs-상담봇", 1);
        Job child = job(owner, "cs-상담봇", 2, parent.getId(), true);
        em.flush();

        detail(child.getId())
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.adapterName").value("cs-상담봇"))
                .andExpect(jsonPath("$.adapterVersion").value(2))
                .andExpect(jsonPath("$.parentJobId").value(parent.getId()))
                .andExpect(jsonPath("$.hasAdapter").value(true))
                // 이어서 학습은 올릴 파일이 없다 — adapterUploaded 는 거짓이고 parentJobId 로 구분한다
                .andExpect(jsonPath("$.adapterUploaded").value(false));

        mockMvc.perform(get("/api/jobs?size=50").header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.content[?(@.jobId == " + child.getId() + ")].adapterName").value("cs-상담봇"))
                .andExpect(jsonPath("$.content[?(@.jobId == " + child.getId() + ")].adapterVersion").value(2));
    }

    // ── 예치 게이트 ──────────────────────────────────────────

    @Test
    @DisplayName("이어서 학습은 올릴 어댑터가 없어도 예치가 열린다 — 입력은 부모의 결과물")
    void continuedTrainingNeedsNoUpload() throws Exception {
        Job parent = completedJob(owner, "cs-상담봇", 1);
        Job child = job(owner, "cs-상담봇", 2, parent.getId(), true);
        em.flush();
        em.clear();

        Job stored = jobRepository.findById(child.getId()).orElseThrow();
        assertThat(stored.isAdapterRequired()).isTrue();
        assertThat(stored.hasAdapterFile()).isFalse();        // 올린 파일은 없는데
        assertThat(stored.isAdapterInputReady()).isTrue();    // 입력은 준비돼 있다
        assertThat(stored.isContinuedTraining()).isTrue();
    }

    @Test
    @DisplayName("파일로 시작하는 Job 은 업로드 전이면 입력이 준비되지 않은 것")
    void uploadedAdapterNeedsFile() {
        Job job = job(owner, "파일로 시작", 1, null, true);
        em.flush();
        assertThat(job.isAdapterInputReady()).isFalse();

        job.recordAdapterUpload("jobs/" + job.getId() + "/input/adapter.tar", 4096L, "ab".repeat(32),
                LocalDateTime.now());
        assertThat(job.isAdapterInputReady()).isTrue();
    }

    @Test
    @DisplayName("어댑터를 안 쓰는 Job 은 언제나 입력 준비 완료 — 기존 동작 그대로")
    void noAdapterIsAlwaysReady() {
        Job job = job(owner, "그냥-lora", 1, null, false);
        em.flush();
        assertThat(job.isAdapterInputReady()).isTrue();
    }

    // ── 이름 변경 ────────────────────────────────────────────

    @Test
    @DisplayName("이름 변경은 상태와 무관하다 — 완료된 Job 을 받기 직전에 고칠 수 있다")
    void renameAnyState() throws Exception {
        Job done = completedJob(owner, "cs-상담봇", 1);
        rename(done.getId(), "상담봇 개편", ownerToken)
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.adapterName").value("상담봇 개편"))
                .andExpect(jsonPath("$.adapterVersion").value(1));   // 버전·계보는 그대로

        Job pending = job(owner, "예치 대기중", 1, null, false);
        em.flush();
        rename(pending.getId(), "미리 정한 이름", ownerToken)
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.adapterName").value("미리 정한 이름"));
    }

    @Test
    @DisplayName("파일 이름으로 위험한 값은 400 — 이름이 그대로 다운로드 파일 이름이 된다")
    void renameRejectsUnsafeNames() throws Exception {
        Job job = completedJob(owner, "cs-상담봇", 1);
        for (String bad : new String[]{"bad/name", "..", ".hidden", "-flag"}) {
            rename(job.getId(), bad, ownerToken)
                    .andExpect(status().isBadRequest())
                    .andExpect(jsonPath("$.code").value("INVALID_ADAPTER_NAME"));
        }
        // 빈 값은 @NotBlank 가 먼저 잡는다
        rename(job.getId(), "   ", ownerToken).andExpect(status().isBadRequest());
    }

    @Test
    @DisplayName("남의 어댑터 이름은 바꿀 수 없다 — 존재 여부도 흘리지 않는다(404)")
    void renameRejectsOtherOwner() throws Exception {
        Job job = completedJob(owner, "cs-상담봇", 1);
        User other = userRepository.save(User.createUser(
                "other-" + UUID.randomUUID() + "@gshared.io", "encoded", "다른사람"));
        rename(job.getId(), "가로채기", jwtProvider.createAccessToken(other.getId(), other.getRole()))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("JOB_NOT_FOUND"));
    }

    // ── 워커 계약 ────────────────────────────────────────────

    @Test
    @DisplayName("execution-context: 이어서 학습이면 부모 결과물의 서명 URL·해시·크기가 adapter 로 나간다")
    void executionContextUsesParentResult() throws Exception {
        RegisterWorkerResponse worker = worker();

        Job parent = completedJob(owner, "cs-상담봇", 1);
        Job child = job(owner, "cs-상담봇", 2, parent.getId(), true);
        em.flush();
        jobRepository.fundIfCreated(child.getId(), 31221L, null, "17", null);
        jobRepository.assignIfFunded(child.getId(), worker.workerId());
        em.clear();

        mockMvc.perform(get("/api/worker/jobs/" + child.getId() + "/execution-context")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + worker.workerToken()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.adapter.sha256").value(RESULT_SHA))
                .andExpect(jsonPath("$.adapter.sizeBytes").value(4096))
                .andExpect(jsonPath("$.adapter.downloadUrl").value(org.hamcrest.Matchers.containsString(
                        "/api/storage/jobs/" + parent.getId() + "/result.tar?method=GET")));
    }

    @Test
    @DisplayName("execution-context: 올린 어댑터면 그 파일의 서명 URL·해시·크기가 나간다")
    void executionContextUsesUploadedAdapter() throws Exception {
        RegisterWorkerResponse worker = worker();

        Job job = job(owner, "파일로 시작", 1, null, true);
        job.recordAdapterUpload("jobs/" + job.getId() + "/input/adapter.tar", 8192L, "ab".repeat(32),
                LocalDateTime.now());
        em.flush();
        jobRepository.fundIfCreated(job.getId(), 31221L, null, "19", null);
        jobRepository.assignIfFunded(job.getId(), worker.workerId());
        em.clear();

        mockMvc.perform(get("/api/worker/jobs/" + job.getId() + "/execution-context")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + worker.workerToken()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.adapter.sha256").value("ab".repeat(32)))
                .andExpect(jsonPath("$.adapter.sizeBytes").value(8192))
                .andExpect(jsonPath("$.adapter.downloadUrl").value(org.hamcrest.Matchers.containsString(
                        "/api/storage/jobs/" + job.getId() + "/input/adapter.tar?method=GET")));
    }

    @Test
    @DisplayName("execution-context: 어댑터를 안 쓰는 Job 은 adapter 가 null — 필드는 남는다")
    void executionContextAdapterNullWhenUnused() throws Exception {
        RegisterWorkerResponse worker = worker();

        Job plain = job(owner, "그냥-lora", 1, null, false);
        em.flush();
        jobRepository.fundIfCreated(plain.getId(), 31221L, null, "18", null);
        jobRepository.assignIfFunded(plain.getId(), worker.workerId());
        em.clear();

        mockMvc.perform(get("/api/worker/jobs/" + plain.getId() + "/execution-context")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + worker.workerToken()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.adapter").doesNotExist())   // null 로 직렬화된다
                .andExpect(jsonPath("$.dataset.sha256").exists());
    }
}
