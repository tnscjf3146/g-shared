package com.gshared.back.job.service;

import com.gshared.back.dataset.domain.Dataset;
import com.gshared.back.dataset.repository.DatasetRepository;
import com.gshared.back.global.client.SchedulerClient;
import com.gshared.back.global.client.SchedulerErrorCode;
import com.gshared.back.global.exception.BusinessException;
import com.gshared.back.job.dto.request.CreateJobRequest;
import com.gshared.back.job.exception.JobErrorCode;
import com.gshared.back.user.domain.User;
import com.gshared.back.user.repository.UserRepository;
import com.gshared.back.worker.domain.Worker;
import com.gshared.back.worker.repository.WorkerRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDateTime;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

/**
 * Job 생성의 검증·오류 경로 테스트. (V6 seed 의 qlora-sft-v1 / qwen2.5-7b 카탈로그 사용)
 * SchedulerClient 는 목으로 대체 — 외부 AI 없이 오류 매핑을 검증한다.
 */
@SpringBootTest
@Transactional
class JobCreateValidationTest {

    @Autowired
    private JobService jobService;
    @Autowired
    private UserRepository userRepository;
    @Autowired
    private DatasetRepository datasetRepository;
    @Autowired
    private WorkerRepository workerRepository;
    @Autowired
    private ObjectMapper objectMapper;

    @MockitoBean
    private SchedulerClient schedulerClient;   // 외부 AI 를 목으로 — 백엔드가 AI 없이도 검증 가능한 이유

    private User owner;
    private Long datasetId;

    @BeforeEach
    void setUp() {
        owner = userRepository.save(User.createUser(
                "create-" + UUID.randomUUID() + "@gshared.io", "encoded", "생성테스터"));
        datasetId = datasetRepository.save(Dataset.create(
                owner, 52_428_800L, 10_000, 3_500_000L, 2_874_100L, 350.0, 1800, null, null)).getId();
    }

    private CreateJobRequest customRequest(String configJson) {
        return new CreateJobRequest("qlora-sft-v1", "qwen2.5-7b", datasetId,
                "CUSTOM", null, configJson == null ? null : objectMapper.readTree(configJson));
    }

    private void addOnlineWorker() {
        Worker worker = workerRepository.save(Worker.register(
                "worker-create-" + UUID.randomUUID(), owner, "hash-" + UUID.randomUUID(),
                "테스트PC", "Windows 11", "RTX 4090", 24_564, "551.23", 72_000.0));
        worker.goOnline(LocalDateTime.now());
        workerRepository.flush();   // 목 검증 전에 DB 반영
    }

    @Test
    @DisplayName("CUSTOM 설정이 누락되면 INVALID_TRAINING_CONFIG")
    void custom_config_missing_field() {
        assertThatThrownBy(() -> jobService.create(owner.getId(),
                customRequest("{\"epochs\":3,\"batchSize\":8}")))   // learningRate·maxLength 누락
                .isInstanceOf(BusinessException.class)
                .hasFieldOrPropertyWithValue("errorCode", JobErrorCode.INVALID_TRAINING_CONFIG);
    }

    @Test
    @DisplayName("CUSTOM 설정에 0 이하 값이 있으면 INVALID_TRAINING_CONFIG")
    void custom_config_non_positive() {
        assertThatThrownBy(() -> jobService.create(owner.getId(),
                customRequest("{\"epochs\":0,\"batchSize\":8,\"learningRate\":0.0002,\"maxLength\":1024}")))
                .isInstanceOf(BusinessException.class)
                .hasFieldOrPropertyWithValue("errorCode", JobErrorCode.INVALID_TRAINING_CONFIG);
    }

    @Test
    @DisplayName("가용 워커가 없으면 NO_AVAILABLE_WORKER (409 — 기다리면 해결)")
    void no_available_worker() {
        // 워커를 하나도 안 만듦 (있어도 OFFLINE 이면 후보 제외)
        assertThatThrownBy(() -> jobService.create(owner.getId(),
                customRequest("{\"epochs\":3,\"batchSize\":8,\"learningRate\":0.0002,\"maxLength\":1024}")))
                .isInstanceOf(BusinessException.class)
                .hasFieldOrPropertyWithValue("errorCode", JobErrorCode.NO_AVAILABLE_WORKER);
    }

    @Test
    @DisplayName("Scheduler 장애(503·타임아웃)면 SCHEDULER_UNAVAILABLE 이 그대로 전달된다")
    void scheduler_unavailable_propagates() {
        addOnlineWorker();
        when(schedulerClient.recommend(any()))
                .thenThrow(new BusinessException(SchedulerErrorCode.SCHEDULER_UNAVAILABLE));

        assertThatThrownBy(() -> jobService.create(owner.getId(),
                customRequest("{\"epochs\":3,\"batchSize\":8,\"learningRate\":0.0002,\"maxLength\":1024}")))
                .isInstanceOf(BusinessException.class)
                .hasFieldOrPropertyWithValue("errorCode", SchedulerErrorCode.SCHEDULER_UNAVAILABLE);
    }
}
