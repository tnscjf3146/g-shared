package com.gshared.back.job.service;

import com.gshared.back.dataset.domain.Dataset;
import com.gshared.back.dataset.repository.DatasetRepository;
import com.gshared.back.global.client.SchedulerClient;
import com.gshared.back.global.client.dto.SchedulerRecommendResponse;
import com.gshared.back.global.exception.BusinessException;
import com.gshared.back.job.domain.Job;
import com.gshared.back.job.dto.request.CreateJobRequest;
import com.gshared.back.job.dto.response.CreateJobResponse;
import com.gshared.back.job.exception.JobErrorCode;
import com.gshared.back.job.repository.JobRepository;
import com.gshared.back.user.domain.User;
import com.gshared.back.user.repository.UserRepository;
import com.gshared.back.worker.domain.GpuGrade;
import com.gshared.back.worker.domain.Worker;
import com.gshared.back.worker.repository.WorkerRepository;
import com.gshared.back.workload.repository.BaseModelRepository;
import com.gshared.back.workload.repository.WorkloadRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

/**
 * 수요자가 고른 GPU 로 Job 이 만들어지는가.
 *
 * <p>추천 화면은 후보를 순위로 보여주고 사용자가 그중 하나를 누른다. 그 선택이 주문에 실리지
 * 않으면 화면은 고를 수 있다고 말하면서 실제로는 1 순위로 배정된다 — 4050 을 눌렀는데 4070 이
 * 도는 일이 실제로 있었다. 이 클래스는 그 경로를 고정한다.
 *
 * <p><b>클라이언트가 보내는 것은 선택뿐이다.</b> 예상 시간·비용·정산 지갑은 서버가 다시 계산한
 * 값을 쓴다({@code JobService#create} 의 "클라이언트 값 안 믿음" 원칙). 그래서 선택은 방금 받은
 * 추천 결과 안에 그 워커가 실제로 있는지로 검증하고, 저장되는 숫자는 <b>고른 워커의</b> 것이어야
 * 한다 — 1 순위의 숫자를 그대로 두면 배정만 바뀌고 예상 시간과 지갑은 남의 것이 된다.
 */
@SpringBootTest
@Transactional
class JobGpuSelectionTest {

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
    private tools.jackson.databind.ObjectMapper objectMapper;

    @MockitoBean
    private SchedulerClient schedulerClient;

    private User owner;
    private Long datasetId;
    private Long workloadId;
    private Long baseModelId;

    /** 1 순위로 추천될 빠른 GPU. */
    private String fastWorkerId;
    /** 사용자가 고를 느린 GPU. */
    private String slowWorkerId;

    private static final String FAST_GPU = "RTX 4070";
    private static final String SLOW_GPU = "RTX 4050 Laptop";

    private static final String CUSTOM_CONFIG =
            "{\"epochs\":3,\"batchSize\":8,\"learningRate\":0.0002,\"maxLength\":1024}";

    private static final double FAST_RUNTIME_SEC = 120.0;
    private static final double SLOW_RUNTIME_SEC = 480.0;

    @BeforeEach
    void setUp() {
        owner = userRepository.save(User.createUser(
                "pick-" + UUID.randomUUID() + "@gshared.io", "encoded", "선택테스터"));
        owner.linkWallet("0x" + "a".repeat(40));   // provider 정산 주소 — 없으면 후보에서 빠진다
        datasetId = datasetRepository.save(Dataset.create(
                owner, 52_428_800L, 10_000, 3_500_000L, 2_874_100L, 350.0, 1800, null, null)).getId();
        workloadId = workloadRepository.findBySlug("qlora-sft-v1").orElseThrow().getId();
        baseModelId = baseModelRepository.findBySlug("qwen3-0.6b").orElseThrow().getId();

        fastWorkerId = onlineWorker(FAST_GPU, 12_282, 72_000.0);
        slowWorkerId = onlineWorker(SLOW_GPU, 6_141, 41_000.0);
        workerRepository.flush();

        // 스케줄러는 언제나 빠른 쪽을 1 순위로 준다. 사용자가 무엇을 고르든 서버는 추천을 다시 받는다.
        when(schedulerClient.recommend(any())).thenReturn(new SchedulerRecommendResponse(
                "runtime-xgb-v1", fastWorkerId, 4_500,
                List.of(new SchedulerRecommendResponse.Ranking(1, fastWorkerId, FAST_RUNTIME_SEC),
                        new SchedulerRecommendResponse.Ranking(2, slowWorkerId, SLOW_RUNTIME_SEC))));
    }

    private String onlineWorker(String gpuModel, int vramMb, double benchmark) {
        Worker worker = workerRepository.save(Worker.register(
                "worker-" + UUID.randomUUID(), owner, "hash-" + UUID.randomUUID(),
                "0x" + "b".repeat(40), "0.1.0", "Windows 11", true,
                gpuModel, vramMb, benchmark, null, "transformer-bench-v1"));
        worker.goOnline(LocalDateTime.now());
        return worker.getId();
    }

    /** CUSTOM 으로 고정 — AUTO 는 설정까지 Scheduler 가 정하고, 여기서 보려는 건 GPU 선택뿐이다. */
    private CreateJobRequest request(Long gpuId) {
        return new CreateJobRequest(workloadId, baseModelId, datasetId, "CUSTOM", null,
                objectMapper.readTree(CUSTOM_CONFIG), null, null, null, gpuId);
    }

    /** 화면이 고르는 단위 — 모델명으로 묶은 등급. */
    private static Long grade(String gpuModel) {
        return GpuGrade.idOf(gpuModel);
    }

    private Job created(CreateJobResponse response) {
        return jobRepository.findById(response.jobId()).orElseThrow();
    }

    @Test
    @DisplayName("고른 워커가 배정 대상이 된다 — 1순위가 아니라")
    void selected_worker_is_the_one_assigned() {
        Job job = created(jobService.create(owner.getId(), request(grade(SLOW_GPU))));

        assertThat(job.getRecommendedWorkerId()).isEqualTo(slowWorkerId);
    }

    @Test
    @DisplayName("예상 시간도 고른 워커의 것이다 — 1순위 시간을 붙여두면 화면이 거짓말을 한다")
    void predicted_runtime_follows_the_selection() {
        Job job = created(jobService.create(owner.getId(), request(grade(SLOW_GPU))));

        assertThat(job.getPredictedRuntimeSec()).isEqualTo(SLOW_RUNTIME_SEC);
    }

    @Test
    @DisplayName("정산 지갑도 고른 워커의 것이다 — 체인이 예치 때 이 값을 잠근다")
    void provider_wallet_follows_the_selection() {
        String expected = workerRepository.findById(slowWorkerId).orElseThrow()
                .getOwner().getWalletAddress();

        Job job = created(jobService.create(owner.getId(), request(grade(SLOW_GPU))));

        assertThat(job.getProviderWallet()).isEqualToIgnoringCase(expected);
    }

    @Test
    @DisplayName("고르지 않으면 지금까지처럼 1순위를 쓴다")
    void no_selection_keeps_the_recommendation() {
        Job job = created(jobService.create(owner.getId(), request(null)));

        assertThat(job.getRecommendedWorkerId()).isEqualTo(fastWorkerId);
        assertThat(job.getPredictedRuntimeSec()).isEqualTo(FAST_RUNTIME_SEC);
    }

    @Test
    @DisplayName("등급에 대기 워커가 여러 대면 순위가 가장 높은 한 대를 고른다")
    void best_ranked_worker_within_the_grade_wins() {
        // 같은 등급(같은 모델명)의 두 번째 워커 — 등급은 모델명으로 묶이므로 이 둘은 한 칸이다
        String siblingId = onlineWorker(SLOW_GPU, 6_141, 39_000.0);
        workerRepository.flush();
        // 형제가 rankings 에 먼저 오지만 순위는 더 낮다 — 목록 순서가 아니라 rank 로 골라야 한다
        when(schedulerClient.recommend(any())).thenReturn(new SchedulerRecommendResponse(
                "runtime-xgb-v1", fastWorkerId, 4_500,
                List.of(new SchedulerRecommendResponse.Ranking(3, siblingId, 900.0),
                        new SchedulerRecommendResponse.Ranking(1, fastWorkerId, FAST_RUNTIME_SEC),
                        new SchedulerRecommendResponse.Ranking(2, slowWorkerId, SLOW_RUNTIME_SEC))));

        Job job = created(jobService.create(owner.getId(), request(grade(SLOW_GPU))));

        assertThat(job.getRecommendedWorkerId()).isEqualTo(slowWorkerId);
        assertThat(job.getPredictedRuntimeSec()).isEqualTo(SLOW_RUNTIME_SEC);
    }

    @Test
    @DisplayName("고른 워커가 추천 결과에 없으면 409 — 그 사이 바빠졌거나 돌릴 수 없는 GPU다")
    void selection_outside_the_ranking_is_rejected() {
        // 워커는 있는데 Scheduler 가 이 설정으로 못 돌린다고 판단한 등급(rankings 에 없다)
        onlineWorker("RTX 3060", 12_288, 30_000.0);
        workerRepository.flush();

        assertThatThrownBy(() -> jobService.create(owner.getId(), request(grade("RTX 3060"))))
                .isInstanceOf(BusinessException.class)
                .hasFieldOrPropertyWithValue("errorCode", JobErrorCode.SELECTED_WORKER_UNAVAILABLE);
    }

    @Test
    @DisplayName("존재하지 않는 등급을 골라도 같은 409 — 없는 id 로 후보를 떠볼 수 없다")
    void unknown_grade_id_is_rejected() {
        assertThatThrownBy(() -> jobService.create(owner.getId(), request(999_999_999L)))
                .isInstanceOf(BusinessException.class)
                .hasFieldOrPropertyWithValue("errorCode", JobErrorCode.SELECTED_WORKER_UNAVAILABLE);
    }
}
