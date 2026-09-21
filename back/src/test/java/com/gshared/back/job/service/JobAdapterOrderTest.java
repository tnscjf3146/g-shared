package com.gshared.back.job.service;

import com.gshared.back.dataset.domain.Dataset;
import com.gshared.back.dataset.repository.DatasetRepository;
import com.gshared.back.global.client.SchedulerClient;
import com.gshared.back.global.client.dto.SchedulerRecommendResponse;
import com.gshared.back.global.exception.BusinessException;
import com.gshared.back.job.domain.Job;
import com.gshared.back.job.domain.JobStatus;
import com.gshared.back.job.dto.request.CreateJobRequest;
import com.gshared.back.job.dto.response.CreateJobResponse;
import com.gshared.back.job.exception.JobErrorCode;
import com.gshared.back.job.repository.JobRepository;
import com.gshared.back.user.domain.User;
import com.gshared.back.user.repository.UserRepository;
import com.gshared.back.worker.domain.Worker;
import com.gshared.back.worker.repository.WorkerRepository;
import com.gshared.back.workload.repository.BaseModelRepository;
import com.gshared.back.workload.repository.WorkloadRepository;
import jakarta.persistence.EntityManager;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

/**
 * POST /jobs 의 어댑터 주문 부분 — 이름·버전·계보를 <b>서버가</b> 정한다.
 *
 * <p>확인하는 것: ① 세 필드를 안 보낸 예전 요청이 그대로 동작하는가(하위 호환),
 * ② 이어서 학습의 부모가 "내 것 · 완료 · 결과물 있음 · 학습 작업"인가, ③ 버전이 부모 + 1 인가.
 * SchedulerClient 는 목 — 외부 AI 없이 주문 규칙만 본다.
 */
@SpringBootTest
@Transactional
class JobAdapterOrderTest {

    @Autowired
    private JobService jobService;
    @Autowired
    private UserRepository userRepository;
    @Autowired
    private DatasetRepository datasetRepository;
    @Autowired
    private WorkerRepository workerRepository;
    @Autowired
    private JobRepository jobRepository;
    @Autowired
    private WorkloadRepository workloadRepository;
    @Autowired
    private BaseModelRepository baseModelRepository;
    @Autowired
    private EntityManager em;
    @Autowired
    private tools.jackson.databind.ObjectMapper objectMapper;

    @MockitoBean
    private SchedulerClient schedulerClient;

    private User owner;
    private Long datasetId;
    private Long workloadId;
    private Long baseModelId;
    private String workerId;

    @BeforeEach
    void setUp() {
        owner = userRepository.save(User.createUser(
                "order-" + UUID.randomUUID() + "@gshared.io", "encoded", "주문테스터"));
        datasetId = datasetRepository.save(Dataset.create(
                owner, 52_428_800L, 10_000, 3_500_000L, 2_874_100L, 350.0, 1800, null, null)).getId();
        workloadId = workloadRepository.findBySlug("qlora-sft-v1").orElseThrow().getId();
        baseModelId = baseModelRepository.findBySlug("qwen3-0.6b").orElseThrow().getId();

        owner.linkWallet("0x" + "a".repeat(40));
        Worker worker = workerRepository.save(Worker.register(
                "worker-order-" + UUID.randomUUID(), owner, "hash-" + UUID.randomUUID(),
                "0x" + "b".repeat(40), "0.1.0", "Windows 11", true,
                "RTX 4090", 24_564, 72_000.0, null, "transformer-bench-v1"));
        worker.goOnline(LocalDateTime.now());
        workerRepository.flush();
        workerId = worker.getId();

        when(schedulerClient.recommend(any())).thenAnswer(invocation -> new SchedulerRecommendResponse(
                "runtime-xgb-v1", workerId,
                List.of(new SchedulerRecommendResponse.Ranking(1, workerId, 985.882))));
    }

    /** CUSTOM 으로 보낸다 — AUTO 는 Scheduler 의 training-config 까지 불러야 해서, 여기 관심사(어댑터 주문)와 멀다. */
    private CreateJobRequest request(Boolean hasAdapter, String adapterName, Long parentJobId) {
        return new CreateJobRequest(workloadId, baseModelId, datasetId, "CUSTOM", null,
                objectMapper.readTree("{\"epochs\":3,\"batchSize\":8,\"learningRate\":0.0002,\"maxLength\":1024}"),
                hasAdapter, adapterName, parentJobId);
    }

    private Job created(CreateJobResponse response) {
        em.flush();
        em.clear();
        return jobRepository.findById(response.jobId()).orElseThrow();
    }

    /** 결과물까지 올라온 완료 Job — 이어서 학습의 출발점이 될 수 있는 상태. */
    private Job completedParent(User user, String adapterName, int version, Long parentWorkloadId) {
        Dataset dataset = datasetRepository.save(Dataset.create(
                user, 100L, 2, 8L, 8L, 4.0, 4, null, null));
        Job job = jobRepository.save(Job.create(user, parentWorkloadId, baseModelId, dataset.getId(),
                "AUTO", 1, 16, 0.0002, 512, 985.882, "runtime-xgb-v1", null, 9L));
        job.assignAdapterOrder(false, adapterName, version, null);
        job.recordResultUpload("jobs/" + job.getId() + "/result.tar", 4096L, "1a".repeat(32));
        ReflectionTestUtils.setField(job, "status", JobStatus.COMPLETED);
        em.flush();
        return job;
    }

    // ── 하위 호환 ────────────────────────────────────────────

    @Test
    @DisplayName("세 필드를 안 보낸 예전 요청도 그대로 성공한다 — 어댑터 없음·v1·모델 기반 기본 이름")
    void backwardCompatible() {
        Job job = created(jobService.create(owner.getId(), request(null, null, null)));

        assertThat(job.isAdapterRequired()).isFalse();
        assertThat(job.getAdapterVersion()).isEqualTo(1);
        assertThat(job.getParentJobId()).isNull();
        assertThat(job.getAdapterName()).isEqualTo("qwen3-0.6b-lora");
        assertThat(job.isAdapterInputReady()).isTrue();   // 올릴 것이 없으니 바로 예치 가능
    }

    @Test
    @DisplayName("hasAdapter=true 면 파일을 기다린다 — 응답에도 확정된 이름·버전이 실린다")
    void uploadedAdapterOrder() {
        CreateJobResponse response = jobService.create(owner.getId(), request(true, "파일로 시작", null));

        assertThat(response.adapterName()).isEqualTo("파일로 시작");
        assertThat(response.adapterVersion()).isEqualTo(1);

        Job job = created(response);
        assertThat(job.isAdapterRequired()).isTrue();
        assertThat(job.isContinuedTraining()).isFalse();
        assertThat(job.isAdapterInputReady()).isFalse();   // 업로드 전이라 예치가 막힌다
    }

    // ── 이어서 학습 ──────────────────────────────────────────

    @Test
    @DisplayName("이어서 학습: 버전은 부모 + 1, 이름을 안 보내면 부모 이름을 잇는다")
    void continuedTrainingBumpsVersion() {
        Job v1 = completedParent(owner, "cs-상담봇", 1, workloadId);
        Job v2 = created(jobService.create(owner.getId(), request(null, null, v1.getId())));

        assertThat(v2.getAdapterVersion()).isEqualTo(2);
        assertThat(v2.getAdapterName()).isEqualTo("cs-상담봇");     // 같은 계보는 같은 이름
        assertThat(v2.getParentJobId()).isEqualTo(v1.getId());
        assertThat(v2.isAdapterRequired()).isTrue();               // hasAdapter 를 안 보내도 어댑터를 쓴다
        assertThat(v2.isAdapterInputReady()).isTrue();             // 부모 결과물이 입력이라 올릴 것이 없다

        // v2 를 완료시키고 다시 이어서 → v3
        ReflectionTestUtils.setField(v2, "status", JobStatus.COMPLETED);
        v2.recordResultUpload("jobs/" + v2.getId() + "/result.tar", 4096L, "2b".repeat(32));
        em.flush();
        Job v3 = created(jobService.create(owner.getId(), request(null, null, v2.getId())));
        assertThat(v3.getAdapterVersion()).isEqualTo(3);
        assertThat(v3.getParentJobId()).isEqualTo(v2.getId());
    }

    @Test
    @DisplayName("이어서 학습해도 이름은 새로 정할 수 있다 — 계보는 parentJobId 가 잡는다")
    void continuedTrainingCanRename() {
        Job v1 = completedParent(owner, "cs-상담봇", 1, workloadId);
        Job v2 = created(jobService.create(owner.getId(), request(null, "상담봇 개편", v1.getId())));

        assertThat(v2.getAdapterName()).isEqualTo("상담봇 개편");
        assertThat(v2.getAdapterVersion()).isEqualTo(2);
        assertThat(v2.getParentJobId()).isEqualTo(v1.getId());
    }

    @Test
    @DisplayName("부모는 내 것이어야 한다 — 남의 Job 은 존재 여부도 흘리지 않고 404")
    void parentMustBeMine() {
        User other = userRepository.save(User.createUser(
                "other-" + UUID.randomUUID() + "@gshared.io", "encoded", "다른사람"));
        Job theirs = completedParent(other, "남의-lora", 1, workloadId);

        assertThatThrownBy(() -> jobService.create(owner.getId(), request(null, null, theirs.getId())))
                .isInstanceOf(BusinessException.class)
                .hasFieldOrPropertyWithValue("errorCode", JobErrorCode.PARENT_JOB_NOT_FOUND);
        assertThatThrownBy(() -> jobService.create(owner.getId(), request(null, null, 999_999L)))
                .isInstanceOf(BusinessException.class)
                .hasFieldOrPropertyWithValue("errorCode", JobErrorCode.PARENT_JOB_NOT_FOUND);
    }

    @Test
    @DisplayName("부모는 완료된 작업이어야 한다 — 학습 중인 어댑터는 이어받을 수 없다")
    void parentMustBeCompleted() {
        Job running = completedParent(owner, "cs-상담봇", 1, workloadId);
        ReflectionTestUtils.setField(running, "status", JobStatus.RUNNING);
        em.flush();

        assertThatThrownBy(() -> jobService.create(owner.getId(), request(null, null, running.getId())))
                .isInstanceOf(BusinessException.class)
                .hasFieldOrPropertyWithValue("errorCode", JobErrorCode.PARENT_JOB_NOT_COMPLETED);
    }

    @Test
    @DisplayName("부모에 결과물이 없으면 이어받을 입력이 없다 — 404")
    void parentMustHaveResult() {
        Job noResult = completedParent(owner, "cs-상담봇", 1, workloadId);
        ReflectionTestUtils.setField(noResult, "resultObjectKey", null);
        em.flush();

        assertThatThrownBy(() -> jobService.create(owner.getId(), request(null, null, noResult.getId())))
                .isInstanceOf(BusinessException.class)
                .hasFieldOrPropertyWithValue("errorCode", JobErrorCode.PARENT_ADAPTER_NOT_FOUND);
    }

    @Test
    @DisplayName("부모는 어댑터를 만드는 작업(학습)이어야 한다 — 추론 결과물에는 어댑터가 없다")
    void parentMustBeTrainingWorkload() {
        // 지금 카탈로그에는 TRAIN_LORA 하나뿐이라(V12 seed) 추론 워크로드를 직접 넣어 확인한다.
        // jobs.workload_id 에 FK 가 걸려 있어 "없는 워크로드"로는 이 분기를 만들 수 없다.
        em.createNativeQuery("""
                INSERT INTO workloads (slug, kind, name, image_digest, min_vram_mb, enabled, created_at, updated_at)
                VALUES ('inference-test', 'INFERENCE', '추론(테스트)', 'sha256:0000000000000000000000000000000000000000000000000000000000000000', 8192, 1, NOW(6), NOW(6))
                """).executeUpdate();
        Long inferenceWorkloadId = workloadRepository.findBySlug("inference-test").orElseThrow().getId();

        Job inference = completedParent(owner, "추론-결과", 1, inferenceWorkloadId);
        assertThatThrownBy(() -> jobService.create(owner.getId(), request(null, null, inference.getId())))
                .isInstanceOf(BusinessException.class)
                .hasFieldOrPropertyWithValue("errorCode", JobErrorCode.PARENT_ADAPTER_NOT_FOUND);
    }

    @Test
    @DisplayName("부모는 같은 베이스 모델로 학습한 것이어야 한다 — 다른 모델에 얹은 어댑터는 Runtime 이 거절한다")
    void parentMustShareBaseModel() {
        Long otherBaseModelId = baseModelRepository.findBySlug("qwen3-1.7b").orElseThrow().getId();
        Job onOtherModel = completedParent(owner, "cs-상담봇", 1, workloadId);
        ReflectionTestUtils.setField(onOtherModel, "baseModelId", otherBaseModelId);
        em.flush();

        assertThatThrownBy(() -> jobService.create(owner.getId(), request(null, null, onOtherModel.getId())))
                .isInstanceOf(BusinessException.class)
                .hasFieldOrPropertyWithValue("errorCode", JobErrorCode.PARENT_BASE_MODEL_MISMATCH);
    }

    // ── 이름 규칙 ────────────────────────────────────────────

    @Test
    @DisplayName("파일 이름으로 위험하거나 너무 긴 이름은 한 코드로 거절한다")
    void rejectsUnsafeName() {
        for (String bad : new String[]{"bad/name", "..", ".hidden", "-flag", "x".repeat(61)}) {
            assertThatThrownBy(() -> jobService.create(owner.getId(), request(null, bad, null)))
                    .as("이름 %s", bad)
                    .isInstanceOf(BusinessException.class)
                    .hasFieldOrPropertyWithValue("errorCode", JobErrorCode.INVALID_ADAPTER_NAME);
        }
    }

    @Test
    @DisplayName("공백만 보낸 이름은 \"안 보냄\"과 같다 — 기본값이 붙는다")
    void blankNameFallsBackToDefault() {
        Job job = created(jobService.create(owner.getId(), request(null, "   ", null)));
        assertThat(job.getAdapterName()).isEqualTo("qwen3-0.6b-lora");
    }
}
