package com.gshared.back.global.client;

import com.gshared.back.global.client.dto.SchedulerRecommendRequest;
import com.gshared.back.global.client.dto.SchedulerRecommendResponse;
import com.gshared.back.global.client.dto.SchedulerTrainingConfig;
import com.gshared.back.global.client.dto.SchedulerTrainingConfigRequest;
import com.gshared.back.global.exception.BusinessException;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.http.HttpStatus;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;

/**
 * AI Scheduler(FastAPI)를 부르는 전화기.
 * Backend 는 AI 내부(XGBoost 등)를 모른다 — HTTP 계약만 지키면 된다.
 *
 * <p>타임아웃 필수: Scheduler 가 느려질 때 Job 생성 스레드가 묶이지 않게 한다.
 * <p>503/timeout 시 "benchmark 높은 GPU 대체 추천" 금지 —
 *    예측 근거 없는 답이 예측처럼 보이게 된다. 그대로 UNAVAILABLE 로 올린다.
 */
@Slf4j
@Component
@EnableConfigurationProperties(SchedulerProperties.class)
public class SchedulerClient {

    private final RestClient restClient;

    public SchedulerClient(SchedulerProperties properties) {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(properties.connectTimeout());
        factory.setReadTimeout(properties.readTimeout());

        this.restClient = RestClient.builder()
                .baseUrl(properties.baseUrl())
                .requestFactory(factory)
                .build();
    }

    /** GPU 추천 요청. 422 → SCHEDULER_REJECTED, 503·timeout → SCHEDULER_UNAVAILABLE. */
    public SchedulerRecommendResponse recommend(SchedulerRecommendRequest request) {
        return call("/scheduler/recommend", request, SchedulerRecommendResponse.class);
    }

    /** AUTO 모드 학습 설정 확정 요청. */
    public SchedulerTrainingConfig trainingConfig(SchedulerTrainingConfigRequest request) {
        return call("/scheduler/training-config", request, SchedulerTrainingConfig.class);
    }

    private <T> T call(String path, Object body, Class<T> responseType) {
        try {
            return restClient.post()
                    .uri(path)
                    .body(body)
                    .retrieve()
                    .body(responseType);
        } catch (RestClientResponseException e) {
            // detail 은 로그로 남긴다 (원인 추적용) — 응답 메시지는 ErrorCode 의 고정 문구
            log.warn("[Scheduler] {} 응답 {} body={}", path, e.getStatusCode(), e.getResponseBodyAsString());
            if (e.getStatusCode() == HttpStatus.UNPROCESSABLE_ENTITY) {
                throw new BusinessException(SchedulerErrorCode.SCHEDULER_REJECTED);
            }
            throw new BusinessException(SchedulerErrorCode.SCHEDULER_UNAVAILABLE);   // 503 포함 그 외 전부
        } catch (ResourceAccessException e) {
            // 연결 거부·타임아웃
            log.warn("[Scheduler] {} 연결 실패: {}", path, e.getMessage());
            throw new BusinessException(SchedulerErrorCode.SCHEDULER_UNAVAILABLE);
        }
    }
}
