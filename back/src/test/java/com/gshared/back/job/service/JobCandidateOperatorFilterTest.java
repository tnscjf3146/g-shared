package com.gshared.back.job.service;

import com.gshared.back.dataset.domain.Dataset;
import com.gshared.back.dataset.repository.DatasetRepository;
import com.gshared.back.global.blockchain.EscrowChainClient;
import com.gshared.back.global.client.SchedulerClient;
import com.gshared.back.global.client.SchedulerErrorCode;
import com.gshared.back.global.client.dto.SchedulerRecommendRequest;
import com.gshared.back.global.exception.BusinessException;
import com.gshared.back.job.dto.request.CreateJobRequest;
import com.gshared.back.job.exception.JobErrorCode;
import com.gshared.back.user.domain.User;
import com.gshared.back.user.repository.UserRepository;
import com.gshared.back.worker.domain.Worker;
import com.gshared.back.worker.repository.WorkerRepository;
import com.gshared.back.workload.repository.BaseModelRepository;
import com.gshared.back.workload.repository.WorkloadRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDateTime;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.BDDMockito.given;
import static org.mockito.BDDMockito.then;
import static org.mockito.Mockito.never;

/**
 * 매칭 후보의 operator 승인 필터 — "ONLINE" 만으로는 후보가 아니다.
 *
 * <p>operator-status 화면이 뭐라고 했든 여기서는 배정 직전에 체인에 다시 묻는다. 화면은 UX 이고,
 * 이 필터가 권위다. 승인 없는 워커에게 배정하면 acceptJob 이 되돌려지고 고객 예치금이 묶인다.
 *
 * <p>Scheduler 는 후보를 받았는지 확인하는 용도로만 쓴다: 후보가 넘어가면 recommend 가 불리고,
 * 후보가 없으면 그 전에 NO_AVAILABLE_WORKER 로 끝난다.
 */
@SpringBootTest
@Transactional
class JobCandidateOperatorFilterTest {

    private static final String PROVIDER_WALLET = "0x9cb91880cd04a6b3bc8cb81d908fe19060260af9";
    private static final String OPERATOR = "0x0394494f68abc014e42879563156b7b19b6fe815";
    private static final String CUSTOM_CONFIG =
            "{\"epochs\":3,\"batchSize\":8,\"learningRate\":0.0002,\"maxLength\":1024}";

    @Autowired
    private JobService jobService;
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
    @MockitoBean
    private EscrowChainClient chain;

    private User owner;
    private CreateJobRequest request;
    private Worker worker;

    @BeforeEach
    void setUp() {
        owner = userRepository.save(User.createUser(
                "filter-" + UUID.randomUUID() + "@gshared.io", "encoded", "의뢰자겸공급자"));
        Long datasetId = datasetRepository.save(Dataset.create(
                owner, 52_428_800L, 10_000, 3_500_000L, 2_874_100L, 350.0, 1800, null, null)).getId();
        Long workloadId = workloadRepository.findBySlug("qlora-sft-v1").orElseThrow().getId();
        Long baseModelId = baseModelRepository.findBySlug("qwen3-0.6b").orElseThrow().getId();
        request = new CreateJobRequest(workloadId, baseModelId, datasetId,
                "CUSTOM", null, objectMapper.readTree(CUSTOM_CONFIG), null, null, null);

        // 같은 지갑이 의뢰자이자 공급자 — 정상 구성이고, 그래도 승인은 isOperator(providerWallet, operator) 다
        owner.linkWallet(PROVIDER_WALLET);
        worker = workerRepository.save(Worker.register(
                "worker-filter-" + UUID.randomUUID(), owner, "hash-" + UUID.randomUUID(),
                OPERATOR, "0.1.0", "Windows 11", true,
                "NVIDIA GeForce RTX 4070 Laptop GPU", 8188, 900.0, "transformer-bench-v1"));
        worker.goOnline(LocalDateTime.now());
        workerRepository.flush();

        given(chain.isConfigured()).willReturn(true);
        // 후보가 넘어갔는지만 보면 되므로 Scheduler 는 바로 실패시켜 흐름을 끊는다
        given(schedulerClient.recommend(any()))
                .willThrow(new BusinessException(SchedulerErrorCode.SCHEDULER_UNAVAILABLE));
    }

    @Test
    @DisplayName("ONLINE + approved=true → 후보 유지 (Scheduler 에 이 워커가 넘어간다)")
    void onlineAndApprovedIsCandidate() {
        given(chain.isOperator(PROVIDER_WALLET, OPERATOR)).willReturn(Optional.of(true));

        assertThatThrownBy(() -> jobService.create(owner.getId(), request))
                .hasFieldOrPropertyWithValue("errorCode", SchedulerErrorCode.SCHEDULER_UNAVAILABLE);

        ArgumentCaptor<SchedulerRecommendRequest> sent = ArgumentCaptor.forClass(SchedulerRecommendRequest.class);
        then(schedulerClient).should().recommend(sent.capture());
        assertThat(sent.getValue().gpuCandidates())
                .extracting(SchedulerRecommendRequest.GpuCandidate::workerId)
                .containsExactly(worker.getId());
        // 주소 계약: provider = users.wallet_address, operator = workers.operator_address
        then(chain).should().isOperator(PROVIDER_WALLET, OPERATOR);
    }

    @Test
    @DisplayName("ONLINE + approved=false → 후보 제외 → NO_AVAILABLE_WORKER (Scheduler 는 불리지 않는다)")
    void onlineButNotApprovedIsExcluded() {
        given(chain.isOperator(PROVIDER_WALLET, OPERATOR)).willReturn(Optional.of(false));

        assertThatThrownBy(() -> jobService.create(owner.getId(), request))
                .isInstanceOf(BusinessException.class)
                .hasFieldOrPropertyWithValue("errorCode", JobErrorCode.NO_AVAILABLE_WORKER);

        then(schedulerClient).should(never()).recommend(any());
    }

    @Test
    @DisplayName("OFFLINE + approved=true → 후보 제외")
    void offlineIsExcluded() {
        worker.goOffline();
        workerRepository.flush();
        given(chain.isOperator(PROVIDER_WALLET, OPERATOR)).willReturn(Optional.of(true));

        assertThatThrownBy(() -> jobService.create(owner.getId(), request))
                .hasFieldOrPropertyWithValue("errorCode", JobErrorCode.NO_AVAILABLE_WORKER);

        then(schedulerClient).should(never()).recommend(any());
    }

    @Test
    @DisplayName("BUSY(작업 중) + approved=true → 후보 제외")
    void busyIsExcluded() {
        worker.startJob(42L);
        workerRepository.flush();
        given(chain.isOperator(PROVIDER_WALLET, OPERATOR)).willReturn(Optional.of(true));

        assertThatThrownBy(() -> jobService.create(owner.getId(), request))
                .hasFieldOrPropertyWithValue("errorCode", JobErrorCode.NO_AVAILABLE_WORKER);

        then(schedulerClient).should(never()).recommend(any());
    }

    @Test
    @DisplayName("체인이 답하지 않으면 거르지 않는다 — RPC 장애로 멀쩡한 공급자가 조용히 빠지면 안 된다")
    void unknownIsNotExcluded() {
        given(chain.isOperator(PROVIDER_WALLET, OPERATOR)).willReturn(Optional.empty());

        assertThatThrownBy(() -> jobService.create(owner.getId(), request))
                .hasFieldOrPropertyWithValue("errorCode", SchedulerErrorCode.SCHEDULER_UNAVAILABLE);

        then(schedulerClient).should().recommend(any());
    }
}
