package com.gshared.back.job.service;

import com.gshared.back.global.client.SchedulerClient;
import com.gshared.back.global.client.SchedulerErrorCode;
import com.gshared.back.global.client.dto.SchedulerRecommendRequest;
import com.gshared.back.global.client.dto.SchedulerTrainingConfig;
import com.gshared.back.global.client.dto.SchedulerTrainingConfigRequest;
import com.gshared.back.global.exception.BusinessException;
import com.gshared.back.job.exception.JobErrorCode;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;

/**
 * 학습 설정 4값을 확정한다. CUSTOM 이면 사용자 값을 검증하고, AUTO 면 Scheduler 에 물어본다.
 *
 * <p>견적과 주문이 같은 규칙을 써야 하므로 한 곳에 둔다 — 견적에서 통과한 설정이 주문에서
 * 거절당하면 사용자는 무엇이 틀렸는지 알 수 없다.
 */
@Component
@RequiredArgsConstructor
public class TrainingConfigResolver {

    private final SchedulerClient schedulerClient;

    public SchedulerTrainingConfig resolve(String trainingMode, String trainingPriority,
                                           JsonNode customConfig,
                                           SchedulerRecommendRequest.DatasetMeta datasetMeta) {
        if ("CUSTOM".equals(trainingMode)) {
            return validateCustom(customConfig);
        }
        String priority = trainingPriority != null ? trainingPriority : "BALANCED";
        SchedulerTrainingConfig config = schedulerClient.trainingConfig(
                new SchedulerTrainingConfigRequest(datasetMeta, priority));
        // AUTO 응답 형태 검증 — null 필드가 NOT NULL 컬럼 저장에서 터지지 않게
        if (config.epochs() == null || config.batchSize() == null
                || config.learningRate() == null || config.maxLength() == null) {
            throw new BusinessException(SchedulerErrorCode.SCHEDULER_UNAVAILABLE);
        }
        return config;
    }

    private SchedulerTrainingConfig validateCustom(JsonNode c) {
        if (c == null || !c.path("epochs").isNumber() || !c.path("batchSize").isNumber()
                || !c.path("learningRate").isNumber() || !c.path("maxLength").isNumber()) {
            throw new BusinessException(JobErrorCode.INVALID_TRAINING_CONFIG);
        }
        SchedulerTrainingConfig config = new SchedulerTrainingConfig(
                c.path("epochs").asInt(), c.path("batchSize").asInt(),
                c.path("learningRate").asDouble(), c.path("maxLength").asInt());
        // 양수 검증 — Scheduler 의 422 에만 기대지 않고 우리도 막는다 (0 epochs → 1 TOKEN 견적 방지)
        if (config.epochs() <= 0 || config.batchSize() <= 0
                || config.learningRate() <= 0 || config.maxLength() <= 0) {
            throw new BusinessException(JobErrorCode.INVALID_TRAINING_CONFIG);
        }
        return config;
    }
}
