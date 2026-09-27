package com.gshared.back.job.controller;

import com.gshared.back.global.client.SchedulerClient;
import com.gshared.back.global.client.dto.SchedulerRecommendRequest;
import com.gshared.back.global.client.dto.SchedulerRecommendResponse;
import com.gshared.back.global.client.dto.SchedulerTrainingConfig;
import com.gshared.back.global.security.jwt.JwtProvider;
import com.gshared.back.user.domain.User;
import com.gshared.back.user.repository.UserRepository;
import com.gshared.back.worker.domain.Worker;
import com.gshared.back.worker.domain.WorkerStatus;
import com.gshared.back.worker.dto.request.RegisterWorkerRequest;
import com.gshared.back.worker.dto.response.RegisterWorkerResponse;
import com.gshared.back.worker.repository.WorkerRepository;
import com.gshared.back.worker.service.WorkerService;
import com.gshared.back.workload.domain.BaseModel;
import com.gshared.back.workload.repository.BaseModelRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.annotation.Transactional;

import jakarta.persistence.EntityManager;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.mockito.ArgumentMatchers.any;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.tuple;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;

/**
 * 주문 전 두 단계 — GPU 후보와 견적.
 *
 * <p>Scheduler 는 대역으로 세운다. 여기서 확인하려는 것은 예측값의 품질이 아니라
 * "우리가 무엇을 물어보고, 받은 답을 어떤 모양으로 화면에 내보내는가" 이기 때문이다.
 *
 * <p>둘 다 아무것도 저장하지 않아야 한다 — 사용자가 설정을 만질 때마다 불리는 곳이라
 * 행이 남으면 버려진 주문서가 쌓인다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional
class JobEstimateIntegrationTest {

    private static final String GPU_OPTIONS_URL = "/api/jobs/gpu-options";
    private static final String ESTIMATE_URL = "/api/jobs/estimate";

    @Autowired
    private MockMvc mockMvc;
    @Autowired
    private UserRepository userRepository;
    @Autowired
    private WorkerRepository workerRepository;
    @Autowired
    private WorkerService workerService;
    @Autowired
    private BaseModelRepository baseModelRepository;
    @Autowired
    private JwtProvider jwtProvider;
    @Autowired
    private tools.jackson.databind.ObjectMapper objectMapper;
    @Autowired
    private JdbcTemplate jdbcTemplate;
    @Autowired
    private EntityManager entityManager;

    @MockitoBean
    private SchedulerClient schedulerClient;

    private Long userId;
    private String accessToken;
    private Long workloadId;
    private Long baseModelId;
    private String workerId;

    @BeforeEach
    void setUp() {
        User user = userRepository.save(User.createUser(
                "estimate-" + UUID.randomUUID() + "@gshared.io", "encoded", "의뢰자"));
        userId = user.getId();
        accessToken = jwtProvider.createAccessToken(user.getId(), user.getRole());

        BaseModel baseModel = baseModelRepository.findBySlug("qwen3-0.6b").orElseThrow();
        baseModelId = baseModel.getId();
        workloadId = baseModel.getWorkload().getId();

        // 후보가 되려면 ONLINE 이어야 한다 — 등록 직후는 OFFLINE 이라 배정 대상이 아니다.
        // 후보가 되려면 에스크로 주소 둘(operator·provider 지갑)도 있어야 한다 (규격 §28)
        user.linkWallet("0x" + "a".repeat(40));
        RegisterWorkerResponse registered = workerService.register(user.getId(),
                new RegisterWorkerRequest("0x" + "b".repeat(40), "0.1.0",
                        new RegisterWorkerRequest.SystemInfo("Windows 11", true),
                        new RegisterWorkerRequest.GpuInfo("RTX 4090", 24576),
                        new RegisterWorkerRequest.BenchmarkInfo(1800.0, null, "transformer-bench-v1")));
        workerId = registered.workerId();
        Worker worker = workerRepository.findById(workerId).orElseThrow();
        worker.goOnline(LocalDateTime.now());
        workerRepository.flush();

        given(schedulerClient.trainingConfig(any()))
                .willReturn(new SchedulerTrainingConfig(3, 8, 0.0002, 1024));
        given(schedulerClient.recommend(any()))
                .willReturn(new SchedulerRecommendResponse("runtime-xgb-v1", workerId, null,
                        List.of(new SchedulerRecommendResponse.Ranking(1, workerId, 3600.0))));
    }

    /** 화면이 보내는 명세. datasetId 가 아니라 분석값을 그대로 싣는다(아직 업로드 전이다). */
    private Map<String, Object> spec() {
        return Map.of(
                "workloadId", workloadId,
                "baseModelId", baseModelId,
                "datasetMetadata", Map.of(
                        "sizeBytes", 10_000_000L,
                        "sampleCount", 1000,
                        "totalTokens", 500_000L,
                        "avgSeqLength", 500.0,
                        "maxSeqLength", 1024),
                "trainingMode", "AUTO",
                "autoMode", "BALANCED",
                "hasAdapter", false,
                "ownModel", false);
    }

    private String body(Map<String, Object> value) {
        return objectMapper.writeValueAsString(value);
    }

    // ── POST /api/jobs/gpu-options ──────────────────────────────

    @Test
    @DisplayName("GPU 후보는 등급마다 적합도와 대기 워커 수를 함께 준다")
    void gpuOptions() throws Exception {
        mockMvc.perform(post(GPU_OPTIONS_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body(spec())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(1))
                .andExpect(jsonPath("$[0].model").value("RTX 4090"))
                .andExpect(jsonPath("$[0].vramMb").value(24576))
                // Scheduler 가 1순위로 고른 워커가 속한 등급이다
                .andExpect(jsonPath("$[0].fit").value("RECOMMENDED"))
                // 필요 VRAM 은 워크로드 seed 값(8192)에서 온다
                .andExpect(jsonPath("$[0].requiredVramMb").value(8192))
                .andExpect(jsonPath("$[0].availableWorkers").value(1))
                .andExpect(jsonPath("$[0].reason").isString());
    }

    @Test
    @DisplayName("같은 등급 id 는 몇 번을 물어도 같다 — 화면이 이 값을 견적 요청에 되싣는다")
    void gradeIdIsStable() throws Exception {
        String first = mockMvc.perform(post(GPU_OPTIONS_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body(spec())))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();

        mockMvc.perform(post(GPU_OPTIONS_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body(spec())))
                .andExpect(status().isOk())
                .andExpect(content().json(first));
    }

    /**
     * 드라이버가 "8 GB" 카드를 8188 MiB 로 보고하는 실제 사례. Scheduler 는 nominal 보정으로
     * 1순위로 골랐는데 Backend 가 raw 값으로 다시 비교해 INSUFFICIENT 라고 하던 회귀.
     */
    @Test
    @DisplayName("8188 MiB 카드는 8192 요구에 실행 가능 — 응답 vramMb 는 raw 그대로, 판정만 nominal")
    void nominalVramFit() throws Exception {
        String laptopWorkerId = registerOnlineWorker("NVIDIA GeForce RTX 4070 Laptop GPU", 8188);
        given(schedulerClient.recommend(any()))
                .willReturn(new SchedulerRecommendResponse("runtime-xgb-v1", laptopWorkerId, null,
                        List.of(new SchedulerRecommendResponse.Ranking(1, laptopWorkerId, 3600.0))));

        mockMvc.perform(post(GPU_OPTIONS_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body(spec())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(2))
                // VRAM 큰 순이라 4090 이 먼저, 4070 Laptop 이 두 번째
                .andExpect(jsonPath("$[1].model").value("NVIDIA GeForce RTX 4070 Laptop GPU"))
                .andExpect(jsonPath("$[1].vramMb").value(8188))
                .andExpect(jsonPath("$[1].requiredVramMb").value(8192))
                .andExpect(jsonPath("$[1].fit").value("RECOMMENDED"))
                .andExpect(jsonPath("$[1].reason").value(org.hamcrest.Matchers.not(
                        org.hamcrest.Matchers.containsString("부족"))))
                .andExpect(jsonPath("$[0].fit").value("OK"));
    }

    @Test
    @DisplayName("허용 오차(32 MiB) 밖이면 INSUFFICIENT — 부족량은 GB 로 반올림하지 않는다")
    void insufficientOutsideTolerance() throws Exception {
        registerOnlineWorker("RTX 3070", 8100);

        mockMvc.perform(post(GPU_OPTIONS_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body(spec())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[1].model").value("RTX 3070"))
                .andExpect(jsonPath("$[1].vramMb").value(8100))
                .andExpect(jsonPath("$[1].fit").value("INSUFFICIENT"))
                .andExpect(jsonPath("$[1].reason").value("VRAM 이 92 MiB 부족해 실행할 수 없습니다."));
    }

    // ── Dynamic VRAM: AI 가 Job 별로 계산한 필요 VRAM 이 정본 (2026-09-21 가이드 §10) ─────────

    @Test
    @DisplayName("AI requiredVramMb=4608 이면 6 GB 카드도 후보 — 카탈로그 8192 로 덮어쓰지 않는다")
    void dynamicVramLetsSmallGpuFit() throws Exception {
        String laptopWorkerId = registerOnlineWorker("NVIDIA GeForce RTX 4050 Laptop GPU", 6141, 5878);
        given(schedulerClient.recommend(any()))
                .willReturn(new SchedulerRecommendResponse("runtime-xgb-v1", laptopWorkerId, 4608,
                        List.of(new SchedulerRecommendResponse.Ranking(1, laptopWorkerId, 3600.0))));

        mockMvc.perform(post(GPU_OPTIONS_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body(spec())))
                .andExpect(status().isOk())
                // 응답의 requiredVramMb 와 fit 판정이 같은 값(4608)을 쓴다 — 갈리면 화면 모순
                .andExpect(jsonPath("$[1].model").value("NVIDIA GeForce RTX 4050 Laptop GPU"))
                .andExpect(jsonPath("$[1].requiredVramMb").value(4608))
                .andExpect(jsonPath("$[1].fit").value(org.hamcrest.Matchers.not("INSUFFICIENT")));
    }

    @Test
    @DisplayName("AI requiredVramMb=7168 이면 6 GB 카드는 INSUFFICIENT — 진짜 부족은 그대로 걸러진다")
    void dynamicVramStillBlocksTooSmallGpu() throws Exception {
        String laptopWorkerId = registerOnlineWorker("NVIDIA GeForce RTX 4050 Laptop GPU", 6141, 5878);
        given(schedulerClient.recommend(any()))
                .willReturn(new SchedulerRecommendResponse("runtime-xgb-v1", laptopWorkerId, 7168,
                        List.of(new SchedulerRecommendResponse.Ranking(1, laptopWorkerId, 3600.0))));

        mockMvc.perform(post(GPU_OPTIONS_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body(spec())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[1].requiredVramMb").value(7168))
                .andExpect(jsonPath("$[1].fit").value("INSUFFICIENT"));
    }

    // null fallback(§10 Case 5)은 위 기존 테스트들이 그대로 검증한다 — 전부 requiredVramMb=null 로
    // 응답을 목킹하고 카탈로그 값(8192·6144)을 기대하므로, fallback 이 깨지면 그 테스트들이 깨진다.

    // ── VRAM 하한: 모델 값이 있으면 모델 값, 없으면 워크로드 값 (V21) ─────────

    /**
     * 6 GB 카드(RTX 4050 Laptop, 드라이버 보고 6141 MiB)가 0.6B 작업에서도 후보에 못 오르던 문제.
     * base_models.min_vram_mb 가 NULL 인 동안은 지금과 같아야 하고(8192 → INSUFFICIENT),
     * 값을 넣으면 화면 표시(requiredVramMb)와 Scheduler 에 보내는 값(requirements.minVramMb)이 함께 바뀌어야 한다.
     */
    @Test
    @DisplayName("base_models.min_vram_mb 가 NULL 이면 워크로드 하한(8192) — 6 GB 카드는 INSUFFICIENT")
    void requiredVramFallsBackToWorkloadWhenModelHasNoFloor() throws Exception {
        registerOnlineWorker("NVIDIA GeForce RTX 4050 Laptop GPU", 6141, 5878);

        mockMvc.perform(post(GPU_OPTIONS_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body(spec())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[1].model").value("NVIDIA GeForce RTX 4050 Laptop GPU"))
                .andExpect(jsonPath("$[1].requiredVramMb").value(8192))
                .andExpect(jsonPath("$[1].fit").value("INSUFFICIENT"));

        ArgumentCaptor<SchedulerRecommendRequest> sent = ArgumentCaptor.forClass(SchedulerRecommendRequest.class);
        verify(schedulerClient).recommend(sent.capture());
        assertThat(sent.getValue().requirements().minVramMb()).isEqualTo(8192);
    }

    @Test
    @DisplayName("base_models.min_vram_mb 가 있으면 그 값이 이긴다 — 화면 requiredVramMb 와 Scheduler requirements 가 같이 바뀐다")
    void requiredVramPrefersModelFloor() throws Exception {
        String laptopWorkerId = registerOnlineWorker("NVIDIA GeForce RTX 4050 Laptop GPU", 6141, 5878);
        given(schedulerClient.recommend(any()))
                .willReturn(new SchedulerRecommendResponse("runtime-xgb-v1", laptopWorkerId, null,
                        List.of(new SchedulerRecommendResponse.Ranking(1, laptopWorkerId, 3600.0))));
        setModelVramFloor(baseModelId, 6144);

        mockMvc.perform(post(GPU_OPTIONS_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body(spec())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].requiredVramMb").value(6144))
                .andExpect(jsonPath("$[1].model").value("NVIDIA GeForce RTX 4050 Laptop GPU"))
                .andExpect(jsonPath("$[1].requiredVramMb").value(6144))
                .andExpect(jsonPath("$[1].fit").value("RECOMMENDED"));

        ArgumentCaptor<SchedulerRecommendRequest> sent = ArgumentCaptor.forClass(SchedulerRecommendRequest.class);
        verify(schedulerClient).recommend(sent.capture());
        assertThat(sent.getValue().requirements().minVramMb()).isEqualTo(6144);
    }

    @Test
    @DisplayName("GPU 후보에는 실측 peakVramMb 가 실린다 — 구버전 exe 로 등록한 워커는 null 로 (후보에서 빠지지 않는다)")
    void candidatesCarryBenchmarkPeakVram() throws Exception {
        String measured = registerOnlineWorker("NVIDIA GeForce RTX 4050 Laptop GPU", 6141, 5878);

        mockMvc.perform(post(GPU_OPTIONS_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body(spec())))
                .andExpect(status().isOk());

        ArgumentCaptor<SchedulerRecommendRequest> sent = ArgumentCaptor.forClass(SchedulerRecommendRequest.class);
        verify(schedulerClient).recommend(sent.capture());
        assertThat(sent.getValue().gpuCandidates())
                .extracting(SchedulerRecommendRequest.GpuCandidate::workerId,
                        SchedulerRecommendRequest.GpuCandidate::benchmarkPeakVramMb)
                .containsExactlyInAnyOrder(
                        tuple(workerId, null),        // setUp 의 4090 — peakVramMb 없이 등록 (구버전 exe)
                        tuple(measured, 5878));
    }

    /** 카탈로그는 조회 전용이라 엔티티에 setter 가 없다 — 값 입력은 마이그레이션이 하므로 테스트도 SQL 로 흉내 낸다. */
    private void setModelVramFloor(Long modelId, int minVramMb) {
        jdbcTemplate.update("UPDATE base_models SET min_vram_mb = ? WHERE model_id = ?", minVramMb, modelId);
        entityManager.flush();
        entityManager.clear();   // setUp 에서 읽어 둔 BaseModel 이 1차 캐시에 남아 있어 새 값을 다시 읽게 한다
    }

    private String registerOnlineWorker(String gpuModel, int vramMb) {
        return registerOnlineWorker(gpuModel, vramMb, null);
    }

    private String registerOnlineWorker(String gpuModel, int vramMb, Integer peakVramMb) {
        RegisterWorkerResponse registered = workerService.register(userId,
                new RegisterWorkerRequest("0x" + "c".repeat(40), "0.1.0",
                        new RegisterWorkerRequest.SystemInfo("Windows 11", true),
                        new RegisterWorkerRequest.GpuInfo(gpuModel, vramMb),
                        new RegisterWorkerRequest.BenchmarkInfo(900.0, peakVramMb, "transformer-bench-v1")));
        Worker worker = workerRepository.findById(registered.workerId()).orElseThrow();
        worker.goOnline(LocalDateTime.now());
        workerRepository.flush();
        return registered.workerId();
    }

    @Test
    @DisplayName("일할 수 있는 워커가 없으면 409 — 요청이 틀린 게 아니라 기다리면 되는 상황이다")
    void noAvailableWorker() throws Exception {
        Worker worker = workerRepository.findById(workerId).orElseThrow();
        worker.goOffline();
        workerRepository.flush();

        mockMvc.perform(post(GPU_OPTIONS_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body(spec())))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("NO_AVAILABLE_WORKER"));
    }

    @Test
    @DisplayName("모델이 그 워크로드에 속하지 않으면 404 — 견적까지 통과시켜 놓고 주문에서 막지 않는다")
    void rejectsMismatchedModel() throws Exception {
        Map<String, Object> spec = new java.util.HashMap<>(spec());
        spec.put("baseModelId", 999_999L);

        mockMvc.perform(post(GPU_OPTIONS_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body(spec)))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("BASE_MODEL_NOT_FOUND"));
    }

    // ── POST /api/jobs/estimate ─────────────────────────────────

    @Test
    @DisplayName("견적은 확정된 학습 설정과 예상 시간·비용을 함께 준다")
    void estimate() throws Exception {
        Map<String, Object> request = new java.util.HashMap<>(spec());
        request.put("gpuId", com.gshared.back.worker.domain.GpuGrade.idOf("RTX 4090"));

        mockMvc.perform(post(ESTIMATE_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body(request)))
                .andExpect(status().isOk())
                // AUTO 라 Scheduler 가 정한 값 — 무엇으로 학습되는지 보고 결정하게 한다
                .andExpect(jsonPath("$.trainingConfig.epochs").value(3))
                .andExpect(jsonPath("$.trainingConfig.batchSize").value(8))
                .andExpect(jsonPath("$.predictedRuntimeSec").value(3600.0))
                // 500,000 토큰 × 3 에폭 / 10,000 = 150 TOKEN
                .andExpect(jsonPath("$.estimatedCost").value(150));
    }

    @Test
    @DisplayName("없는 등급으로 견적을 내면 409 — 그 사이 그 PC 들이 다 꺼진 것이다")
    void estimateWithUnknownGrade() throws Exception {
        Map<String, Object> request = new java.util.HashMap<>(spec());
        request.put("gpuId", 1L);

        mockMvc.perform(post(ESTIMATE_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body(request)))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("NO_AVAILABLE_WORKER"));
    }

    @Test
    @DisplayName("CUSTOM 인데 설정이 비면 400 — 견적 단계에서도 주문과 같은 규칙으로 막는다")
    void rejectsEmptyCustomConfig() throws Exception {
        Map<String, Object> request = new java.util.HashMap<>(spec());
        request.put("gpuId", com.gshared.back.worker.domain.GpuGrade.idOf("RTX 4090"));
        request.put("trainingMode", "CUSTOM");
        request.remove("autoMode");

        mockMvc.perform(post(ESTIMATE_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body(request)))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("INVALID_TRAINING_CONFIG"));
    }

    @Test
    @DisplayName("견적을 내도 Job 은 만들어지지 않는다 — 저장은 주문에서만")
    void estimateStoresNothing() throws Exception {
        Map<String, Object> request = new java.util.HashMap<>(spec());
        request.put("gpuId", com.gshared.back.worker.domain.GpuGrade.idOf("RTX 4090"));

        mockMvc.perform(post(ESTIMATE_URL)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body(request)))
                .andExpect(status().isOk());

        mockMvc.perform(get("/api/jobs")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.totalElements").value(0));
    }

    @Test
    @DisplayName("워커가 ONLINE 이어도 로그인하지 않았으면 견적을 받을 수 없다")
    void requiresLogin() throws Exception {
        mockMvc.perform(post(GPU_OPTIONS_URL)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body(spec())))
                .andExpect(status().isUnauthorized());
    }
}
