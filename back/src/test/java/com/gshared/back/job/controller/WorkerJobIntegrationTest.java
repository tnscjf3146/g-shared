package com.gshared.back.job.controller;

import com.gshared.back.dataset.domain.Dataset;
import com.gshared.back.dataset.repository.DatasetRepository;
import com.gshared.back.global.storage.LocalObjectStorage;
import com.gshared.back.job.domain.Job;
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
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.Map;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 워커가 워커 토큰으로 부르는 Job 창구(§11 execution-context · §17/§18 result-upload)와
 * 서명 URL 스토리지(/api/storage/**)를 보안 필터까지 거쳐 확인한다.
 *
 * <p>핵심은 "이 토큰의 워커에게 배정된 Job 인가" 검증과, 업로드 완료 검증이 신고값이 아니라
 * 실제 오브젝트를 보는지다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional
@TestPropertySource(properties = {
        "storage.root=${java.io.tmpdir}/gshared-test-storage",
        "storage.public-base-url=http://localhost:8080",
        "escrow.contract-address=0x" + "e0e0e0e0e0e0e0e0e0e0e0e0e0e0e0e0e0e0e0e0",
        // 주소만 있는 반쪽 설정은 기동이 거부한다(EscrowStartupValidator). 닫힌 포트를 함께 줘서
        // "설정은 됐지만 노드가 없다"로 만든다 — verification-required=false 라 경고만 남기고 뜬다.
        "escrow.rpc-url=http://127.0.0.1:9"
})
class WorkerJobIntegrationTest {

    private static final String CLIENT_WALLET = "0x" + "a".repeat(40);
    private static final String PROVIDER_WALLET = "0x" + "b".repeat(40);
    private static final String OPERATOR = "0x" + "c".repeat(40);
    private static final String DATASET_HASH = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08";

    @Autowired
    private MockMvc mockMvc;
    @Autowired
    private ObjectMapper objectMapper;
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
    private LocalObjectStorage storage;
    @Autowired
    private EntityManager em;

    private User client;
    private String workerId;
    private String workerToken;
    private String otherWorkerToken;
    private Job job;

    @BeforeEach
    void setUp() {
        client = userRepository.save(User.createUser(
                "client-" + UUID.randomUUID() + "@gshared.io", "encoded", "의뢰자"));
        client.linkWallet(CLIENT_WALLET);
        User provider = userRepository.save(User.createUser(
                "provider-" + UUID.randomUUID() + "@gshared.io", "encoded", "공급자"));
        provider.linkWallet(PROVIDER_WALLET);

        RegisterWorkerResponse registered = workerService.register(provider.getId(), registerRequest(OPERATOR));
        workerId = registered.workerId();
        workerToken = registered.workerToken();
        otherWorkerToken = workerService.register(provider.getId(),
                registerRequest("0x" + "d".repeat(40))).workerToken();

        BaseModel baseModel = baseModelRepository.findBySlug("qwen3-0.6b").orElseThrow();
        Dataset dataset = datasetRepository.save(Dataset.create(
                client, 52_428_800L, 10_000, 2_500_000L, 1_800_000L, 250.0, 1024,
                "0x" + DATASET_HASH, "datasets/1/dataset.jsonl"));
        job = jobRepository.save(Job.create(client, baseModel.getWorkload().getId(), baseModel.getId(),
                dataset.getId(), "AUTO", 1, 16, 0.0002, 512, 985.882, "runtime-xgb-v1", workerId, 9L));
        // 데이터셋 원본은 Job 에 묶인다(V15) — 업로드 완료 검증을 거친 키·해시가 execution-context 로 나간다
        job.recordDatasetUpload("jobs/" + job.getId() + "/input/dataset.jsonl", 52_428_800L, DATASET_HASH,
                java.time.LocalDateTime.now());

        // CREATED → FUNDED(escrowJobId=17) → ASSIGNED(이 워커) — 매칭이 끝나 JOB_ASSIGN 을 받은 시점.
        // 조건부 UPDATE 는 clearAutomatically 라 그 전에 지갑 연결 등 대기 중인 변경을 먼저 DB 로 보낸다.
        em.flush();
        jobRepository.fundIfCreated(job.getId(), 31221L, null, "17", null);
        jobRepository.assignIfFunded(job.getId(), workerId);
        em.clear();
    }

    private static RegisterWorkerRequest registerRequest(String operatorAddress) {
        return new RegisterWorkerRequest(operatorAddress, "0.1.0",
                new RegisterWorkerRequest.SystemInfo("Windows 11", true),
                new RegisterWorkerRequest.GpuInfo("RTX 4090", 24576),
                new RegisterWorkerRequest.BenchmarkInfo(42000.0, null, "transformer-bench-v1"));
    }

    private String contextUrl(Long jobId) {
        return "/api/worker/jobs/" + jobId + "/execution-context";
    }

    private String uploadUrl(Long jobId) {
        return "/api/worker/jobs/" + jobId + "/result-upload";
    }

    private static String sha256(byte[] bytes) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    }

    // ── GET /api/worker/jobs/{jobId}/execution-context ──────────

    @Test
    @DisplayName("배정된 워커는 모델·이미지·데이터셋·설정·에스크로 대조값을 한 번에 받는다")
    void executionContext() throws Exception {
        MvcResult result = mockMvc.perform(get(contextUrl(job.getId()))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + workerToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.jobId").value(String.valueOf(job.getId())))   // 규격: 문자열
                .andExpect(jsonPath("$.assignedWorkerId").value(workerId))
                .andExpect(jsonPath("$.model.modelRef").value("Qwen/Qwen3-0.6B"))
                // 커밋 SHA 40자. Worker 의 model_prep.check_revision 은 null 과
                // "main" 을 모두 거절한다 -- 태그는 움직이고, 값이 매겨져 배정된
                // Job 이 다음 주에 다른 가중치로 학습되면 안 되기 때문이다.
                // 카탈로그가 revision 없이 다시 seed 되면 여기서 걸린다.
                .andExpect(jsonPath("$.model.modelRevision")
                        .value(org.hamcrest.Matchers.matchesPattern("[0-9a-f]{40}")))
                // Worker 는 승인된 canonical 이미지만 실행한다 (system/worker/gshared_worker/runtime_image.py).
                // 0.3.0(V19) — 어댑터 이어 학습이 되는 이미지. 0.2.0 으로 되돌아가면 어댑터 Job 이 ADAPTER_UNAVAILABLE 로 죽으므로 여기서 걸린다.
                .andExpect(jsonPath("$.runtimeImage.imageRef").value(
                        "le3560/gshared-qlora-sft-runtime@sha256:e32bd1c1b8e7689c33100eaf1718f3ceadea80ff1d13de4da970173762e8e170"))
                .andExpect(jsonPath("$.runtimeImage.digest").value(
                        "sha256:e32bd1c1b8e7689c33100eaf1718f3ceadea80ff1d13de4da970173762e8e170"))
                .andExpect(jsonPath("$.dataset.sha256").value(DATASET_HASH))          // 0x 벗긴 64자
                .andExpect(jsonPath("$.dataset.downloadUrl").value(org.hamcrest.Matchers.containsString("/api/storage/jobs/" + job.getId() + "/input/dataset.jsonl?method=GET")))
                .andExpect(jsonPath("$.dataset.effectiveTokens").value(1_800_000))
                .andExpect(jsonPath("$.trainingConfig.epochs").value(1))
                .andExpect(jsonPath("$.trainingConfig.maxLength").value(512))
                .andExpect(jsonPath("$.escrow.chainId").value(31221))
                .andExpect(jsonPath("$.escrow.contractAddress").value("0x" + "e0e0e0e0e0e0e0e0e0e0e0e0e0e0e0e0e0e0e0e0"))
                .andExpect(jsonPath("$.escrow.escrowJobId").value("17"))
                .andExpect(jsonPath("$.escrow.client").value(CLIENT_WALLET))
                .andExpect(jsonPath("$.escrow.provider").value(PROVIDER_WALLET))
                .andExpect(jsonPath("$.escrow.expectedPriceToken").value(9))
                .andExpect(jsonPath("$.escrow.expectedPriceWei").value("9000000000000000000"))   // 9 × 10^18, 문자열
                .andExpect(jsonPath("$.escrow.expectedState").value(1))
                .andReturn();

        // externalRef 는 생성 시 발급된 bytes32 — 그대로 실려야 워커가 온체인 값과 대조한다
        JsonNode body = objectMapper.readTree(result.getResponse().getContentAsString());
        assertThat(body.path("escrow").path("externalRef").asString()).matches("^0x[0-9a-f]{64}$");

        // 데이터셋 서명 URL 은 실제로 내려받기가 된다 (파일을 미리 올려 둔 경우)
        storage.write("jobs/" + job.getId() + "/input/dataset.jsonl", new java.io.ByteArrayInputStream("{}".getBytes(StandardCharsets.UTF_8)));
        URI download = URI.create(body.path("dataset").path("downloadUrl").asString());
        mockMvc.perform(get(download.getPath() + "?" + download.getQuery()))
                .andExpect(status().isOk())
                .andExpect(content().bytes("{}".getBytes(StandardCharsets.UTF_8)));
    }

    @Test
    @DisplayName("다른 워커의 토큰이면 404 — 남에게 배정된 Job 은 존재 여부도 흘리지 않는다")
    void executionContextOfAnotherWorkersJob() throws Exception {
        mockMvc.perform(get(contextUrl(job.getId()))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + otherWorkerToken))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("JOB_NOT_FOUND"));
    }

    @Test
    @DisplayName("워커 토큰이 없거나 틀리면 401 — 로그인 JWT 로도 들어올 수 없다")
    void executionContextWithoutWorkerToken() throws Exception {
        mockMvc.perform(get(contextUrl(job.getId())))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("INVALID_WORKER_TOKEN"));

        mockMvc.perform(get(contextUrl(job.getId()))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + UUID.randomUUID()))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("INVALID_WORKER_TOKEN"));
    }

    @Test
    @DisplayName("배정이 풀린(FUNDED) Job 은 404 — 더는 이 워커의 Job 이 아니다")
    void executionContextWhenUnassigned() throws Exception {
        jobRepository.unassignIfAssigned(job.getId(), workerId);
        em.clear();

        // unassign 으로 assignedWorkerId 가 비므로 "이 워커의 Job" 조회 자체가 실패한다 → 404
        mockMvc.perform(get(contextUrl(job.getId()))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + workerToken))
                .andExpect(status().isNotFound());
    }

    // ── POST /api/worker/jobs/{jobId}/result-upload (+ /complete) ─

    @Test
    @DisplayName("업로드 URL 발급 → 서명 URL 로 PUT → 완료 검증까지 한 흐름으로 통과한다")
    void resultUploadRoundTrip() throws Exception {
        jobRepository.startIfAssigned(job.getId(), workerId, "0xaccept", java.time.LocalDateTime.now());   // RUNNING
        em.clear();

        // ① 발급
        MvcResult issued = mockMvc.perform(post(uploadUrl(job.getId()))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + workerToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of(
                                "fileName", "result.tar", "contentType", "application/x-tar"))))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.objectKey").value("jobs/" + job.getId() + "/result.tar"))
                .andExpect(jsonPath("$.uploadUrl").value(org.hamcrest.Matchers.startsWith(
                        "http://localhost:8080/api/storage/jobs/" + job.getId() + "/result.tar?method=PUT&expires=")))
                .andExpect(jsonPath("$.expiresAt").exists())
                .andReturn();
        JsonNode issuedBody = objectMapper.readTree(issued.getResponse().getContentAsString());
        URI upload = URI.create(issuedBody.path("uploadUrl").asString());
        String objectKey = issuedBody.path("objectKey").asString();

        // ② 서명 URL 로 본문을 그대로 PUT
        byte[] artifact = "lora-adapter-bytes".getBytes(StandardCharsets.UTF_8);
        mockMvc.perform(put(upload.getPath() + "?" + upload.getQuery())
                        .contentType(MediaType.APPLICATION_OCTET_STREAM)
                        .content(artifact))
                .andExpect(status().isOk());

        // ③ 완료 — Backend 가 실제 오브젝트의 크기·해시를 대조한다
        mockMvc.perform(post(uploadUrl(job.getId()) + "/complete")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + workerToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of(
                                "objectKey", objectKey,
                                "sizeBytes", artifact.length,
                                "sha256", sha256(artifact)))))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.jobId").value(String.valueOf(job.getId())))
                .andExpect(jsonPath("$.objectKey").value(objectKey))
                .andExpect(jsonPath("$.verified").value(true));

        em.flush();   // 서비스가 엔티티에 남긴 기록을 DB 로 반영한 뒤 다시 읽는다
        em.clear();
        Job stored = jobRepository.findById(job.getId()).orElseThrow();
        assertThat(stored.getResultObjectKey()).isEqualTo(objectKey);
        assertThat(stored.getResultSizeBytes()).isEqualTo((long) artifact.length);
        assertThat(stored.getResultSha256()).isEqualTo(sha256(artifact));
    }

    @Test
    @DisplayName("신고한 해시가 실제 파일과 다르면 422 — 신고값을 믿지 않는다")
    void completeWithWrongHash() throws Exception {
        jobRepository.startIfAssigned(job.getId(), workerId, "0xaccept", java.time.LocalDateTime.now());
        em.clear();
        String objectKey = "jobs/" + job.getId() + "/result.tar";
        byte[] artifact = "real-bytes".getBytes(StandardCharsets.UTF_8);
        storage.write(objectKey, new java.io.ByteArrayInputStream(artifact));

        mockMvc.perform(post(uploadUrl(job.getId()) + "/complete")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + workerToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of(
                                "objectKey", objectKey,
                                "sizeBytes", artifact.length,
                                "sha256", "0".repeat(64)))))
                .andExpect(status().is(422))
                .andExpect(jsonPath("$.code").value("RESULT_UPLOAD_MISMATCH"));
    }

    @Test
    @DisplayName("올라온 파일이 없으면 404 — verified=true 는 실제 오브젝트를 본 뒤에만 나간다")
    void completeWithoutObject() throws Exception {
        jobRepository.startIfAssigned(job.getId(), workerId, "0xaccept", java.time.LocalDateTime.now());
        em.clear();

        mockMvc.perform(post(uploadUrl(job.getId()) + "/complete")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + workerToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of(
                                "objectKey", "jobs/" + job.getId() + "/missing.tar",
                                "sizeBytes", 1,
                                "sha256", "0".repeat(64)))))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("RESULT_OBJECT_NOT_FOUND"));
    }

    @Test
    @DisplayName("아직 RUNNING 이 아닌(ASSIGNED) Job 에는 업로드 URL 을 주지 않는다 — 409")
    void resultUploadBeforeRunning() throws Exception {
        mockMvc.perform(post(uploadUrl(job.getId()))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + workerToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of(
                                "fileName", "result.tar", "contentType", "application/x-tar"))))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("INVALID_JOB_STATUS"));
    }

    // ── /api/storage/** 서명 검증 ────────────────────────────────────

    @Test
    @DisplayName("퍼센트 인코딩된 경로도 서명한 키와 같은 오브젝트로 풀린다 — 인코딩 차이로 서명이 어긋나지 않는다")
    void storageDecodesEncodedObjectKey() throws Exception {
        String objectKey = "jobs/" + job.getId() + "/result-v1.tar";
        storage.write(objectKey, new java.io.ByteArrayInputStream("bytes".getBytes(StandardCharsets.UTF_8)));
        URI signed = URI.create(storage.presignDownload(objectKey, java.time.Duration.ofMinutes(10)).url());

        // 같은 키의 하이픈을 인코딩해 요청해도 서명 검증과 파일 조회가 같은 키로 이뤄진다
        // (인코딩된 슬래시·마침표는 Spring Security 의 StrictHttpFirewall 이 그 전에 400 으로 막는다)
        String encodedPath = "/api/storage/jobs/" + job.getId() + "/result%2Dv1.tar";
        // 문자열로 넘기면 MockMvc 가 % 를 다시 인코딩하므로 URI 객체로 원문 그대로 보낸다
        mockMvc.perform(get(URI.create(encodedPath + "?" + signed.getRawQuery())))
                .andExpect(status().isOk())
                .andExpect(content().bytes("bytes".getBytes(StandardCharsets.UTF_8)));

        // 상위 경로 이동(..)을 인코딩해 숨겨도 400 — 방화벽이 막고, 뚫려도 키 형식 검사가 거른다
        mockMvc.perform(get(URI.create("/api/storage/jobs/%2E%2E/secret?" + signed.getRawQuery())))
                .andExpect(status().isBadRequest());
    }

    @Test
    @DisplayName("서명이 틀리거나 만료된 URL 은 403 — 서명 없이는 아무 키에도 쓰지 못한다")
    void storageRejectsBadSignature() throws Exception {
        mockMvc.perform(put("/api/storage/jobs/1/result.tar?method=PUT&expires=9999999999&sig=deadbeef")
                        .contentType(MediaType.APPLICATION_OCTET_STREAM)
                        .content("x".getBytes(StandardCharsets.UTF_8)))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.code").value("INVALID_SIGNATURE"));

        // 유효한 서명이라도 만료 시각이 지났으면 거절
        String expired = storage.presignUpload("jobs/1/result.tar", "application/x-tar",
                java.time.Duration.ofSeconds(-10)).url();
        URI uri = URI.create(expired);
        mockMvc.perform(put(uri.getPath() + "?" + uri.getQuery())
                        .contentType(MediaType.APPLICATION_OCTET_STREAM)
                        .content("x".getBytes(StandardCharsets.UTF_8)))
                .andExpect(status().isForbidden());
    }
}
