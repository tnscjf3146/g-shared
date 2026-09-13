package com.gshared.back.job.controller;

import com.gshared.back.global.client.SchedulerClient;
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
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.BDDMockito.given;
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

    @MockitoBean
    private SchedulerClient schedulerClient;

    private String accessToken;
    private Long workloadId;
    private Long baseModelId;
    private String workerId;

    @BeforeEach
    void setUp() {
        User user = userRepository.save(User.createUser(
                "estimate-" + UUID.randomUUID() + "@gshared.io", "encoded", "의뢰자"));
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
                        new RegisterWorkerRequest.BenchmarkInfo(1800.0, "transformer-bench-v1")));
        workerId = registered.workerId();
        Worker worker = workerRepository.findById(workerId).orElseThrow();
        worker.goOnline(LocalDateTime.now());
        workerRepository.flush();

        given(schedulerClient.trainingConfig(any()))
                .willReturn(new SchedulerTrainingConfig(3, 8, 0.0002, 1024));
        given(schedulerClient.recommend(any()))
                .willReturn(new SchedulerRecommendResponse("runtime-xgb-v1", workerId,
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
