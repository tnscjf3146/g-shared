package com.gshared.back.job.service;

import com.gshared.back.dataset.domain.Dataset;
import com.gshared.back.dataset.repository.DatasetRepository;
import com.gshared.back.global.client.SchedulerClient;
import com.gshared.back.global.client.dto.SchedulerRecommendRequest;
import com.gshared.back.global.client.dto.SchedulerRecommendResponse;
import com.gshared.back.job.domain.JobStatus;
import com.gshared.back.job.dto.request.CreateJobRequest;
import com.gshared.back.job.dto.response.CreateJobResponse;
import com.gshared.back.job.repository.JobRepository;
import com.gshared.back.user.domain.User;
import com.gshared.back.user.repository.UserRepository;
import com.gshared.back.worker.domain.Worker;
import com.gshared.back.worker.repository.WorkerRepository;
import com.gshared.back.workload.repository.BaseModelRepository;
import com.gshared.back.workload.repository.WorkloadRepository;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * Job 생성이 <b>실제로 INSERT 되는지</b>.
 *
 * <p>일부러 테스트 트랜잭션을 두지 않는다 — {@code @Transactional} 테스트 안에서는 서비스가 바깥 트랜잭션에
 * 참여해 클래스 기본값(readOnly)이 적용되지 않으므로, 읽기 전용 트랜잭션에서 INSERT 가 거절되는 회귀를
 * 잡을 수 없다. 대신 만든 행은 {@link #tearDown()} 에서 직접 지운다.
 */
@SpringBootTest
class JobCreateTransactionTest {

    @Autowired
    private JobService jobService;
    @Autowired
    private JobRepository jobRepository;
    @Autowired
    private UserRepository userRepository;
    @Autowired
    private DatasetRepository datasetRepository;
    @Autowired
    private WorkerRepository workerRepository;
    @Autowired
    private WorkloadRepository workloadRepository;
    @Autowired
    private BaseModelRepository baseModelRepository;
    @Autowired
    private ObjectMapper objectMapper;

    @MockitoBean
    private SchedulerClient schedulerClient;

    private User owner;
    private Long datasetId;
    private String workerId;
    private Long workloadId;
    private Long baseModelId;
    private Long createdJobId;

    @BeforeEach
    void setUp() {
        owner = User.createUser("tx-" + UUID.randomUUID() + "@gshared.io", "encoded", "트랜잭션테스터");
        owner.linkWallet("0x" + UUID.randomUUID().toString().replace("-", "") + "00000000");
        owner = userRepository.save(owner);
        datasetId = datasetRepository.save(Dataset.create(
                owner, 52_428_800L, 10_000, 3_500_000L, 2_874_100L, 350.0, 1800, null, null)).getId();
        workloadId = workloadRepository.findBySlug("qlora-sft-v1").orElseThrow().getId();
        baseModelId = baseModelRepository.findBySlug("qwen3-0.6b").orElseThrow().getId();

        workerId = "worker-tx-" + UUID.randomUUID();
        Worker worker = Worker.register(workerId, owner, "hash-" + UUID.randomUUID(),
                "0x" + "b".repeat(40), "0.1.0", "Windows 11", true,
                "RTX 4090", 24_564, 72_000.0, null, "transformer-bench-v1");
        worker.goOnline(LocalDateTime.now());
        workerRepository.save(worker);
    }

    @AfterEach
    void tearDown() {
        if (createdJobId != null) {
            jobRepository.deleteById(createdJobId);
        }
        workerRepository.deleteById(workerId);
        datasetRepository.deleteById(datasetId);
        userRepository.deleteById(owner.getId());
    }

    @Test
    @DisplayName("POST /api/jobs — 테스트 트랜잭션 없이도 CREATED 행이 커밋되고, recommend 에 minVramMb 가 실린다")
    void create_commits_job_row() {
        when(schedulerClient.recommend(any())).thenReturn(new SchedulerRecommendResponse(
                "runtime-xgb-test", workerId, null,
                List.of(new SchedulerRecommendResponse.Ranking(1, workerId, 1234.5))));

        CreateJobResponse response = jobService.create(owner.getId(), new CreateJobRequest(
                workloadId, baseModelId, datasetId, "CUSTOM", null,
                objectMapper.readTree("{\"epochs\":3,\"batchSize\":8,\"learningRate\":0.0002,\"maxLength\":1024}"),
                null, null, null, null));
        createdJobId = response.jobId();

        // 별도 커넥션에서 다시 읽는다 — 커밋되지 않았으면 여기서 비어 있다
        var saved = jobRepository.findById(createdJobId);
        assertThat(saved).isPresent();
        assertThat(saved.get().getStatus()).isEqualTo(JobStatus.CREATED);
        assertThat(saved.get().getRecommendedWorkerId()).isEqualTo(workerId);
        assertThat(saved.get().getProviderWallet()).isEqualTo(owner.getWalletAddress());

        ArgumentCaptor<SchedulerRecommendRequest> sent = ArgumentCaptor.forClass(SchedulerRecommendRequest.class);
        verify(schedulerClient).recommend(sent.capture());
        Integer catalogMinVram = workloadRepository.findById(workloadId).orElseThrow().getMinVramMb();
        assertThat(sent.getValue().requirements().minVramMb()).isEqualTo(catalogMinVram);
    }
}
