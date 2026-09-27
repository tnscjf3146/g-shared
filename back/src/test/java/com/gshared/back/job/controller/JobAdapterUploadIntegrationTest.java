package com.gshared.back.job.controller;

import com.gshared.back.dataset.domain.Dataset;
import com.gshared.back.dataset.repository.DatasetRepository;
import com.gshared.back.global.security.jwt.JwtProvider;
import com.gshared.back.job.domain.Job;
import com.gshared.back.job.domain.JobStatus;
import com.gshared.back.job.repository.JobRepository;
import com.gshared.back.job.service.JobAdapterService;
import com.gshared.back.user.domain.User;
import com.gshared.back.user.repository.UserRepository;
import com.gshared.back.workload.repository.BaseModelRepository;
import com.gshared.back.workload.repository.WorkloadRepository;
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
import org.springframework.test.web.servlet.ResultActions;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.io.ByteArrayOutputStream;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.LocalDateTime;
import java.util.HexFormat;
import java.util.Map;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 입력 어댑터 업로드 흐름(lora-adapter-backend-작업 §3·§4·§6·§7): adapter-upload 발급 → 서명 URL 로 PUT →
 * complete 검증 → /fund 허용, 그리고 Job 이 끝나면 cleanup 이 파일을 지운다.
 *
 * <p>데이터셋과 다른 점은 <b>대조할 등록 메타가 없다</b>는 것이라, 형식 검사(tar 항목 이름)가 그 자리를 대신한다.
 * 그래서 "어댑터가 아닌 .tar 는 예치 전에 걸러지는가"가 여기서 가장 중요한 확인이다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional
@TestPropertySource(properties = {
        "storage.root=${java.io.tmpdir}/gshared-test-storage",
        "storage.public-base-url=http://localhost:8080"
})
class JobAdapterUploadIntegrationTest {

    private static final int BLOCK = 512;
    private static final byte[] DATASET_FILE =
            "{\"text\":\"hello\"}\n{\"text\":\"world\"}\n".getBytes(StandardCharsets.UTF_8);

    @Autowired
    private MockMvc mockMvc;
    @Autowired
    private ObjectMapper objectMapper;
    @Autowired
    private UserRepository userRepository;
    @Autowired
    private DatasetRepository datasetRepository;
    @Autowired
    private JobRepository jobRepository;
    @Autowired
    private WorkloadRepository workloadRepository;
    @Autowired
    private BaseModelRepository baseModelRepository;
    @Autowired
    private JwtProvider jwtProvider;
    @Autowired
    private JobAdapterService jobAdapterService;
    @Autowired
    private com.gshared.back.global.storage.LocalObjectStorage storage;
    @Autowired
    private EntityManager em;

    private User owner;
    private String ownerToken;
    private Long workloadId;
    private Long baseModelId;

    @BeforeEach
    void setUp() {
        owner = userRepository.save(User.createUser(
                "adapter-" + UUID.randomUUID() + "@gshared.io", "encoded", "어댑터테스터"));
        ownerToken = jwtProvider.createAccessToken(owner.getId(), owner.getRole());
        workloadId = workloadRepository.findBySlug("qlora-sft-v1").orElseThrow().getId();
        baseModelId = baseModelRepository.findBySlug("qwen3-0.6b").orElseThrow().getId();
    }

    // ── tar 만들기 (프런트·Worker 와 같은 규격) ─────────────────

    private static byte[] entry(String name, String body) {
        byte[] header = new byte[BLOCK];
        byte[] nameBytes = name.getBytes(StandardCharsets.UTF_8);
        System.arraycopy(nameBytes, 0, header, 0, nameBytes.length);
        byte[] size = String.format("%011o", body.getBytes(StandardCharsets.UTF_8).length)
                .getBytes(StandardCharsets.UTF_8);
        System.arraycopy(size, 0, header, 124, size.length);
        header[156] = '0';
        byte[] content = body.getBytes(StandardCharsets.UTF_8);
        byte[] out = new byte[BLOCK + (content.length + BLOCK - 1) / BLOCK * BLOCK];
        System.arraycopy(header, 0, out, 0, BLOCK);
        System.arraycopy(content, 0, out, BLOCK, content.length);
        return out;
    }

    private static byte[] tar(byte[]... entries) {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        for (byte[] e : entries) {
            out.writeBytes(e);
        }
        out.writeBytes(new byte[BLOCK * 2]);
        return out.toByteArray();
    }

    private static final byte[] ADAPTER = tar(
            entry("adapter_config.json", "{\"r\":8}"),
            entry("adapter_model.safetensors", "w".repeat(1500)));
    /** 어댑터가 아닌 tar — 형식 검사가 걸러야 한다. */
    private static final byte[] NOT_ADAPTER = tar(entry("readme.txt", "nope"));

    private static String sha256(byte[] bytes) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    }

    // ── 픽스처 ──────────────────────────────────────────────

    /** 데이터셋 업로드까지 끝난 CREATED Job. 어댑터 여부만 인자로 가른다. */
    private Job job(boolean withAdapter) throws Exception {
        Dataset dataset = datasetRepository.save(Dataset.create(
                owner, (long) DATASET_FILE.length, 2, 8L, 8L, 4.0, 4, "0x" + sha256(DATASET_FILE), null));
        Job job = jobRepository.save(Job.create(owner, workloadId, baseModelId, dataset.getId(),
                "AUTO", 1, 16, 0.0002, 512, 985.882, "runtime-xgb-v1", null, 9L));
        job.assignAdapterOrder(withAdapter, "테스트 어댑터", 1, null);
        // 데이터셋은 이 테스트의 관심사가 아니다 — 검증이 끝난 상태로 바로 둔다(/fund 가 어댑터만 보고 판단하게)
        job.recordDatasetUpload("jobs/" + job.getId() + "/input/dataset.jsonl",
                (long) DATASET_FILE.length, sha256(DATASET_FILE), LocalDateTime.now());
        em.flush();
        return job;
    }

    private String url(Long jobId) {
        return "/api/jobs/" + jobId + "/adapter-upload";
    }

    private ResultActions issue(Long jobId) throws Exception {
        return mockMvc.perform(post(url(jobId)).header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken));
    }

    private ResultActions complete(Long jobId) throws Exception {
        return mockMvc.perform(post(url(jobId) + "/complete")
                .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken));
    }

    private ResultActions complete(Long jobId, Map<String, Object> body) throws Exception {
        return mockMvc.perform(post(url(jobId) + "/complete")
                .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken)
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(body)));
    }

    private ResultActions fund(Long jobId) throws Exception {
        return mockMvc.perform(post("/api/jobs/" + jobId + "/fund")
                .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken)
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"txHash\":\"0x" + "ab".repeat(32) + "\",\"escrowJobId\":\"17\"}"));
    }

    /** 발급 → PUT. 올린 파일의 uploadUrl 을 돌려준다. */
    private String upload(Long jobId, byte[] content) throws Exception {
        MvcResult issued = issue(jobId).andExpect(status().isOk()).andReturn();
        JsonNode body = objectMapper.readTree(issued.getResponse().getContentAsString());
        URI uploadUrl = URI.create(body.path("uploadUrl").asString());
        mockMvc.perform(put(uploadUrl.getPath() + "?" + uploadUrl.getQuery())
                        .contentType(MediaType.APPLICATION_OCTET_STREAM)
                        .content(content))
                .andExpect(status().isOk());
        return body.path("objectKey").asString();
    }

    // ── 테스트 ──────────────────────────────────────────────

    @Test
    @DisplayName("발급 → PUT → complete → /fund: 어댑터는 jobs/{id}/input/adapter.tar 에, Job 에 실제 크기·해시가 기록된다")
    void uploadThenFund() throws Exception {
        Job job = job(true);

        // 업로드 전에는 예치 불가 — 워커가 내려받을 입력이 없는 채로 예치금이 묶이면 안 된다
        fund(job.getId()).andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("ADAPTER_NOT_UPLOADED"));
        mockMvc.perform(get("/api/jobs/" + job.getId()).header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken))
                .andExpect(jsonPath("$.hasAdapter").value(true))
                .andExpect(jsonPath("$.adapterUploaded").value(false));

        // ① 발급 — Backend 가 키를 정한다
        issue(job.getId())
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.objectKey").value("jobs/" + job.getId() + "/input/adapter.tar"))
                .andExpect(jsonPath("$.uploadUrl").value(org.hamcrest.Matchers.startsWith(
                        "http://localhost:8080/api/storage/jobs/" + job.getId() + "/input/adapter.tar?method=PUT&expires=")))
                .andExpect(jsonPath("$.expiresAt").exists());

        // ②③ PUT → complete (프런트처럼 본문 없이)
        String objectKey = upload(job.getId(), ADAPTER);
        complete(job.getId())
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.jobId").value(job.getId()))
                .andExpect(jsonPath("$.objectKey").value(objectKey))
                .andExpect(jsonPath("$.verified").value(true));

        em.flush();
        em.clear();
        Job stored = jobRepository.findById(job.getId()).orElseThrow();
        assertThat(stored.hasAdapterFile()).isTrue();
        assertThat(stored.getAdapterObjectKey()).isEqualTo(objectKey);
        assertThat(stored.getAdapterSizeBytes()).isEqualTo((long) ADAPTER.length);
        assertThat(stored.getAdapterSha256()).isEqualTo(sha256(ADAPTER));

        // ④ 이제 예치 가능
        mockMvc.perform(get("/api/jobs/" + job.getId()).header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken))
                .andExpect(jsonPath("$.adapterUploaded").value(true));
        fund(job.getId()).andExpect(status().isOk());
    }

    @Test
    @DisplayName("검증이 끝난 어댑터는 봉인된다 — 살아 있는 URL 로도 바꿔치기할 수 없다")
    void sealedAfterComplete() throws Exception {
        Job job = job(true);
        MvcResult issued = issue(job.getId()).andReturn();
        JsonNode body = objectMapper.readTree(issued.getResponse().getContentAsString());
        URI uploadUrl = URI.create(body.path("uploadUrl").asString());

        mockMvc.perform(put(uploadUrl.getPath() + "?" + uploadUrl.getQuery())
                .contentType(MediaType.APPLICATION_OCTET_STREAM).content(ADAPTER)).andExpect(status().isOk());
        complete(job.getId()).andExpect(status().isOk());

        // 같은(아직 안 만료된) URL 로 다시 올리기 — 봉인이 막는다
        mockMvc.perform(put(uploadUrl.getPath() + "?" + uploadUrl.getQuery())
                        .contentType(MediaType.APPLICATION_OCTET_STREAM).content(NOT_ADAPTER))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("OBJECT_SEALED"));
    }

    @Test
    @DisplayName("어댑터가 아닌 .tar 는 예치 전에 422 로 걸러진다")
    void rejectsWrongFormat() throws Exception {
        Job job = job(true);
        upload(job.getId(), NOT_ADAPTER);
        complete(job.getId())
                .andExpect(status().isUnprocessableContent())
                .andExpect(jsonPath("$.code").value("ADAPTER_INVALID_FORMAT"));
        // 기록되지 않았으므로 예치도 막힌 채다
        fund(job.getId()).andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("ADAPTER_NOT_UPLOADED"));
    }

    @Test
    @DisplayName("신고값이 실제 파일과 다르면 422, 올린 파일이 없으면 404")
    void rejectsMismatchAndMissing() throws Exception {
        Job job = job(true);

        // 아직 아무것도 안 올렸다
        complete(job.getId())
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("ADAPTER_OBJECT_NOT_FOUND"));

        String objectKey = upload(job.getId(), ADAPTER);
        complete(job.getId(), Map.of("objectKey", objectKey, "sizeBytes", ADAPTER.length + 512, "sha256", sha256(ADAPTER)))
                .andExpect(status().isUnprocessableContent())
                .andExpect(jsonPath("$.code").value("ADAPTER_UPLOAD_MISMATCH"));
        complete(job.getId(), Map.of("objectKey", objectKey, "sizeBytes", ADAPTER.length, "sha256", "0".repeat(64)))
                .andExpect(status().isUnprocessableContent())
                .andExpect(jsonPath("$.code").value("ADAPTER_UPLOAD_MISMATCH"));

        // 남의 경로를 내 파일이라 우기기
        complete(job.getId(), Map.of("objectKey", "jobs/999999/input/adapter.tar"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("ADAPTER_OBJECT_KEY_MISMATCH"));

        // 맞는 신고값이면 통과
        complete(job.getId(), Map.of("objectKey", objectKey, "sizeBytes", ADAPTER.length, "sha256", sha256(ADAPTER)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.verified").value(true));
    }

    @Test
    @DisplayName("어댑터를 쓰지 않는 Job 은 발급도 완료도 409 — 주문과 어긋난 요청")
    void rejectsWhenNotExpected() throws Exception {
        Job job = job(false);
        issue(job.getId()).andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("ADAPTER_NOT_EXPECTED"));
        complete(job.getId()).andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("ADAPTER_NOT_EXPECTED"));
        // 그리고 그런 Job 은 어댑터 없이 바로 예치된다 (하위 호환)
        fund(job.getId()).andExpect(status().isOk());
    }

    @Test
    @DisplayName("예치가 끝난 Job 은 어댑터를 바꿀 수 없다 — 워커가 견적과 다른 입력을 받게 된다")
    void rejectsAfterFunded() throws Exception {
        Job job = job(true);
        upload(job.getId(), ADAPTER);
        complete(job.getId()).andExpect(status().isOk());
        fund(job.getId()).andExpect(status().isOk());
        em.flush();

        issue(job.getId()).andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("INVALID_JOB_STATUS"));
    }

    @Test
    @DisplayName("cleanup: 끝난 Job 의 어댑터 파일을 지우고 기록을 남긴다")
    void cleanupDeletesFile() throws Exception {
        Job job = job(true);
        upload(job.getId(), ADAPTER);
        complete(job.getId()).andExpect(status().isOk());
        em.flush();

        // 아직 CREATED 이고 방금 만들었으니 청소 대상이 아니다
        assertThat(jobAdapterService.findFilesToClean()).extracting(Job::getId).doesNotContain(job.getId());

        org.springframework.test.util.ReflectionTestUtils.setField(job, "status", JobStatus.COMPLETED);
        em.flush();
        em.clear();
        assertThat(jobAdapterService.findFilesToClean()).extracting(Job::getId).contains(job.getId());

        jobAdapterService.deleteAdapterFile(job.getId());
        em.flush();
        em.clear();
        Job cleaned = jobRepository.findById(job.getId()).orElseThrow();
        assertThat(cleaned.hasAdapterFile()).isFalse();
        assertThat(cleaned.getAdapterObjectKey()).isNotNull();   // 키·해시는 남아 "무엇이 있었는지"는 추적된다
        assertThat(cleaned.getAdapterSha256()).isNotNull();
        assertThat(jobAdapterService.findFilesToClean()).extracting(Job::getId).doesNotContain(job.getId());
    }

    @Test
    @DisplayName("크기 검사는 해시보다 먼저 — size() 는 파일이 있으면 크기를, 없으면 empty 를 준다")
    void sizeBeforeHash() throws Exception {
        Job job = job(true);
        String objectKey = "jobs/" + job.getId() + "/input/adapter.tar";
        assertThat(storage.size(objectKey)).isEmpty();
        upload(job.getId(), ADAPTER);
        assertThat(storage.size(objectKey)).contains((long) ADAPTER.length);
        // 신고 크기가 다르면 해시(stat)까지 가지 않고 422 — 순서는 코드가, 결과는 여기서 본다
        complete(job.getId(), Map.of("objectKey", objectKey, "sizeBytes", ADAPTER.length + 512))
                .andExpect(status().isUnprocessableContent())
                .andExpect(jsonPath("$.code").value("ADAPTER_UPLOAD_MISMATCH"));
    }

    @Test
    @DisplayName("남의 Job 에는 손댈 수 없다 — 존재 여부도 흘리지 않는다(404)")
    void rejectsOtherOwner() throws Exception {
        Job job = job(true);
        User other = userRepository.save(User.createUser(
                "other-" + UUID.randomUUID() + "@gshared.io", "encoded", "다른사람"));
        String otherToken = jwtProvider.createAccessToken(other.getId(), other.getRole());

        mockMvc.perform(post(url(job.getId())).header(HttpHeaders.AUTHORIZATION, "Bearer " + otherToken))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("JOB_NOT_FOUND"));
    }
}
