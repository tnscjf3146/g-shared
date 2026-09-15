package com.gshared.back.job.controller;

import com.gshared.back.dataset.domain.Dataset;
import com.gshared.back.dataset.repository.DatasetRepository;
import com.gshared.back.global.security.jwt.JwtProvider;
import com.gshared.back.global.storage.LocalObjectStorage;
import com.gshared.back.job.domain.Job;
import com.gshared.back.job.domain.JobStatus;
import com.gshared.back.job.repository.JobRepository;
import com.gshared.back.job.service.JobDatasetService;
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

import java.io.ByteArrayInputStream;
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
 * 데이터셋 원본 업로드 흐름(Dataset Upload Handoff): dataset-upload 발급 → 서명 URL 로 PUT → complete 검증 → /fund 허용,
 * 그리고 Job 이 끝나면 cleanup 이 파일을 지운다.
 * 핵심은 complete 가 신고값이 아니라 실제 오브젝트를 보는지, 등록된 Dataset 메타와 다른 파일을 거절하는지다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional
@TestPropertySource(properties = {
        "storage.root=${java.io.tmpdir}/gshared-test-storage",
        "storage.public-base-url=http://localhost:8080"
})
class JobDatasetUploadIntegrationTest {

    private static final byte[] FILE = "{\"text\":\"hello\"}\n{\"text\":\"world\"}\n".getBytes(StandardCharsets.UTF_8);

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
    private LocalObjectStorage storage;
    @Autowired
    private JobDatasetService jobDatasetService;
    @Autowired
    private EntityManager em;

    private User owner;
    private String ownerToken;
    private Long workloadId;
    private Long baseModelId;
    private String fileHash;

    @BeforeEach
    void setUp() throws Exception {
        owner = userRepository.save(User.createUser(
                "upload-" + UUID.randomUUID() + "@gshared.io", "encoded", "업로드테스터"));
        ownerToken = jwtProvider.createAccessToken(owner.getId(), owner.getRole());
        workloadId = workloadRepository.findBySlug("qlora-sft-v1").orElseThrow().getId();
        baseModelId = baseModelRepository.findBySlug("qwen3-0.6b").orElseThrow().getId();
        fileHash = sha256(FILE);
    }

    private static String sha256(byte[] bytes) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    }

    /** 프런트가 파일을 분석해 등록한 메타 — 크기·해시가 FILE 과 같다 (dataHash 는 0x 붙은 표기). */
    private Job createdJob(long sizeBytes, String dataHash) {
        Dataset dataset = datasetRepository.save(Dataset.create(
                owner, sizeBytes, 2, 8L, 8L, 4.0, 4, dataHash, null));
        return jobRepository.save(Job.create(owner, workloadId, baseModelId, dataset.getId(),
                "AUTO", 1, 16, 0.0002, 512, 985.882, "runtime-xgb-v1", null, 9L));
    }

    private Job createdJob() {
        return createdJob(FILE.length, "0x" + fileHash);
    }

    private String issueUrl(Long jobId) {
        return "/api/jobs/" + jobId + "/dataset-upload";
    }

    private ResultActions issue(Long jobId) throws Exception {
        return mockMvc.perform(post(issueUrl(jobId))
                .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken)
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(Map.of(
                        "fileName", "dataset.jsonl", "contentType", "application/jsonl"))));
    }

    private ResultActions complete(Long jobId, String objectKey, long sizeBytes, String sha256) throws Exception {
        return mockMvc.perform(post(issueUrl(jobId) + "/complete")
                .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken)
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(Map.of(
                        "objectKey", objectKey, "sizeBytes", sizeBytes, "sha256", sha256))));
    }

    private ResultActions fund(Long jobId) throws Exception {
        return mockMvc.perform(post("/api/jobs/" + jobId + "/fund")
                .header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken)
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"txHash\":\"0x" + "ab".repeat(32) + "\",\"escrowJobId\":\"17\"}"));
    }

    @Test
    @DisplayName("발급 → PUT → complete → /fund: 원본은 jobs/{id}/input/ 아래, Job 에 실제 파일의 크기·해시가 기록된다")
    void uploadThenFund() throws Exception {
        Job job = createdJob();

        // 업로드 전에는 예치 불가
        fund(job.getId()).andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("DATASET_NOT_UPLOADED"));
        mockMvc.perform(get("/api/jobs/" + job.getId()).header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken))
                .andExpect(jsonPath("$.datasetUploaded").value(false));

        // ① 발급 — Backend 가 키를 정한다
        MvcResult issued = issue(job.getId())
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.objectKey").value("jobs/" + job.getId() + "/input/dataset.jsonl"))
                .andExpect(jsonPath("$.uploadUrl").value(org.hamcrest.Matchers.startsWith(
                        "http://localhost:8080/api/storage/jobs/" + job.getId() + "/input/dataset.jsonl?method=PUT&expires=")))
                .andExpect(jsonPath("$.expiresAt").exists())
                .andReturn();
        JsonNode body = objectMapper.readTree(issued.getResponse().getContentAsString());
        URI upload = URI.create(body.path("uploadUrl").asString());
        String objectKey = body.path("objectKey").asString();

        // ② 서명 URL 로 본문을 그대로 PUT (로그인 토큰 없이 — 서명이 곧 인증)
        mockMvc.perform(put(upload.getPath() + "?" + upload.getQuery())
                        .contentType(MediaType.APPLICATION_OCTET_STREAM)
                        .content(FILE))
                .andExpect(status().isOk());

        // ③ complete — 실제 오브젝트를 대조 (대문자 해시도 받아 소문자로 정규화)
        complete(job.getId(), objectKey, FILE.length, fileHash.toUpperCase())
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.jobId").value(job.getId()))
                .andExpect(jsonPath("$.objectKey").value(objectKey))
                .andExpect(jsonPath("$.verified").value(true));

        em.flush();
        em.clear();
        Job stored = jobRepository.findById(job.getId()).orElseThrow();
        assertThat(stored.hasDatasetFile()).isTrue();
        assertThat(stored.getDatasetObjectKey()).isEqualTo(objectKey);
        assertThat(stored.getDatasetSizeBytes()).isEqualTo((long) FILE.length);
        assertThat(stored.getDatasetSha256()).isEqualTo(fileHash);

        // ④ 이제 예치 가능
        mockMvc.perform(get("/api/jobs/" + job.getId()).header(HttpHeaders.AUTHORIZATION, "Bearer " + ownerToken))
                .andExpect(jsonPath("$.datasetUploaded").value(true));
        fund(job.getId()).andExpect(status().isOk());
    }

    @Test
    @DisplayName("파일을 올리지 않고 complete 하면 404 — 신고만으로는 업로드가 되지 않는다")
    void completeWithoutFile() throws Exception {
        Job job = createdJob();
        complete(job.getId(), "jobs/" + job.getId() + "/input/dataset.jsonl", FILE.length, fileHash)
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("DATASET_OBJECT_NOT_FOUND"));
    }

    @Test
    @DisplayName("신고한 해시·크기가 실제 파일과 다르면 422 DATASET_UPLOAD_MISMATCH")
    void completeWithWrongReport() throws Exception {
        Job job = createdJob();
        String objectKey = "jobs/" + job.getId() + "/input/dataset.jsonl";
        storage.write(objectKey, new ByteArrayInputStream(FILE));

        complete(job.getId(), objectKey, FILE.length, "0".repeat(64))
                .andExpect(status().is(422))
                .andExpect(jsonPath("$.code").value("DATASET_UPLOAD_MISMATCH"));
        complete(job.getId(), objectKey, FILE.length + 1, fileHash)
                .andExpect(status().is(422))
                .andExpect(jsonPath("$.code").value("DATASET_UPLOAD_MISMATCH"));
        assertThat(jobRepository.findById(job.getId()).orElseThrow().hasDatasetFile()).isFalse();
    }

    @Test
    @DisplayName("올린 파일이 등록된 Dataset 메타(크기·해시)와 다르면 422 DATASET_METADATA_MISMATCH — 견적은 그 메타로 계산됐다")
    void completeWithFileDifferentFromMetadata() throws Exception {
        Job job = createdJob(FILE.length, "0x" + "1".repeat(64));   // 메타의 해시가 다른 파일을 가리킨다
        String objectKey = "jobs/" + job.getId() + "/input/dataset.jsonl";
        storage.write(objectKey, new ByteArrayInputStream(FILE));

        complete(job.getId(), objectKey, FILE.length, fileHash)   // 신고값은 실제 파일과 일치
                .andExpect(status().is(422))
                .andExpect(jsonPath("$.code").value("DATASET_METADATA_MISMATCH"));
    }

    @Test
    @DisplayName("남의 Job 경로의 키는 400, 남의 Job 은 404, 예치 뒤(FUNDED)에는 409 — 파일을 바꿔치기할 틈이 없다")
    void guards() throws Exception {
        Job job = createdJob();
        complete(job.getId(), "jobs/" + (job.getId() + 1) + "/input/dataset.jsonl", FILE.length, fileHash)
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("DATASET_OBJECT_KEY_MISMATCH"));

        User other = userRepository.save(User.createUser(
                "other-" + UUID.randomUUID() + "@gshared.io", "encoded", "남"));
        String otherToken = jwtProvider.createAccessToken(other.getId(), other.getRole());
        mockMvc.perform(post(issueUrl(job.getId()))
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + otherToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(Map.of(
                                "fileName", "dataset.jsonl", "contentType", "application/jsonl"))))
                .andExpect(status().isNotFound());

        job.recordDatasetUpload("jobs/" + job.getId() + "/input/dataset.jsonl", (long) FILE.length, fileHash, LocalDateTime.now());
        em.flush();
        jobRepository.fundIfCreated(job.getId(), 31221L, null, "17", "0x" + "cd".repeat(32));
        em.clear();
        issue(job.getId()).andExpect(status().isConflict());
    }

    @Test
    @DisplayName("Job 이 끝나면(CANCELLED) cleanup 이 원본을 지우고 기록한다 — 원본은 영구 보관하지 않는다")
    void cleanupDeletesFileOfFinishedJob() throws Exception {
        Job job = createdJob();
        String objectKey = "jobs/" + job.getId() + "/input/dataset.jsonl";
        storage.write(objectKey, new ByteArrayInputStream(FILE));
        job.recordDatasetUpload(objectKey, (long) FILE.length, fileHash, LocalDateTime.now());
        em.flush();

        // 아직 CREATED 이고 방치 기준(24h)도 안 지났으니 대상이 아니다
        assertThat(jobDatasetService.findFilesToClean()).extracting(Job::getId).doesNotContain(job.getId());

        jobRepository.cancelIfUnassigned(job.getId(), owner.getId(), LocalDateTime.now());   // CREATED → CANCELLED
        em.clear();
        assertThat(jobDatasetService.findFilesToClean()).extracting(Job::getId).contains(job.getId());

        jobDatasetService.deleteDatasetFile(job.getId());
        em.flush();
        em.clear();

        assertThat(storage.stat(objectKey)).isEmpty();
        Job cleaned = jobRepository.findById(job.getId()).orElseThrow();
        assertThat(cleaned.getStatus()).isEqualTo(JobStatus.CANCELLED);
        assertThat(cleaned.hasDatasetFile()).isFalse();
        assertThat(cleaned.getDatasetDeletedAt()).isNotNull();
        assertThat(cleaned.getDatasetObjectKey()).isEqualTo(objectKey);   // 키는 남는다
        assertThat(jobDatasetService.findFilesToClean()).extracting(Job::getId).doesNotContain(job.getId());
        jobDatasetService.deleteDatasetFile(job.getId());   // 두 번 지워도 안전
    }
}
