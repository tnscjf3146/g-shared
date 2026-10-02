package com.gshared.back.global.client;

import com.gshared.back.global.client.dto.SchedulerRecommendRequest;
import com.gshared.back.global.client.dto.SchedulerRecommendResponse;
import com.gshared.back.global.client.dto.SchedulerTrainingConfig;
import com.gshared.back.global.client.dto.SchedulerTrainingConfigRequest;
import com.gshared.back.global.exception.BusinessException;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.http.client.JdkClientHttpRequestFactory;
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

    /** AI 가 409 본문 detail[].type 에 싣는 값. ai/app/api/scheduler.py 의 recommend 참조 */
    private static final String NO_FEASIBLE_GPU_TYPE = "\"no_feasible_gpu\"";

    private final RestClient restClient;

    public SchedulerClient(SchedulerProperties properties) {
        // 자바 내장 HttpClient 기반 팩토리. connect 는 HttpClient 가, read 는 팩토리가 담당.
        java.net.http.HttpClient httpClient = java.net.http.HttpClient.newBuilder()
                .connectTimeout(properties.connectTimeout())
                .build();
        JdkClientHttpRequestFactory factory = new JdkClientHttpRequestFactory(httpClient);
        factory.setReadTimeout(properties.readTimeout());

        this.restClient = RestClient.builder()
                .baseUrl(properties.baseUrl())
                .requestFactory(factory)
                .build();
    }

    /** GPU 추천 요청. 409 no_feasible_gpu → NO_FEASIBLE_GPU, 그 밖의 4xx → SCHEDULER_REJECTED, 503·timeout → SCHEDULER_UNAVAILABLE. */
    public SchedulerRecommendResponse recommend(SchedulerRecommendRequest request) {
        return call("/scheduler/recommend", request, SchedulerRecommendResponse.class);
    }

    /** AUTO 모드 학습 설정 확정 요청. */
    public SchedulerTrainingConfig trainingConfig(SchedulerTrainingConfigRequest request) {
        return call("/scheduler/training-config", request, SchedulerTrainingConfig.class);
    }

    private <T> T call(String path, Object body, Class<T> responseType) {
        try {
            T response = restClient.post()
                    .uri(path)
                    .body(body)
                    .retrieve()
                    .body(responseType);
            if (response == null) {
                // 2xx 인데 body 없음 — 계약 위반. 미매핑 500 으로 새지 않게 여기서 차단
                log.warn("[Scheduler] {} 2xx 응답이지만 body 없음", path);
                throw new BusinessException(SchedulerErrorCode.SCHEDULER_UNAVAILABLE);
            }
            return response;
        } catch (RestClientResponseException e) {
            // detail 은 로그로 남긴다 (원인 추적용) — 응답 메시지는 ErrorCode 의 고정 문구
            String responseBody = e.getResponseBodyAsString();
            log.warn("[Scheduler] {} 응답 {} body={}", path, e.getStatusCode(), responseBody);
            // 409 no_feasible_gpu = "이번 후보 GPU 로는 안 돌아간다"(scheduler-api-contract.md §409).
            // 4xx 로 뭉뚱그리면 사용자는 "요청을 거절했습니다"만 보고 무엇을 바꿔야 할지 모른다.
            // 본문 type 까지 확인한다 — AI 가 다른 409 를 추가해도 추측해서 매핑하지 않게.
            if (e.getStatusCode().value() == 409 && responseBody.contains(NO_FEASIBLE_GPU_TYPE)) {
                throw new BusinessException(SchedulerErrorCode.NO_FEASIBLE_GPU);
            }
            if (e.getStatusCode().is4xxClientError()) {
                // 422(검증 거절)뿐 아니라 400·404(경로/계약 불일치)도 "재시도로 해결 안 됨" 쪽으로
                throw new BusinessException(SchedulerErrorCode.SCHEDULER_REJECTED);
            }
            throw new BusinessException(SchedulerErrorCode.SCHEDULER_UNAVAILABLE);   // 5xx
        } catch (ResourceAccessException e) {
            // 연결 거부·타임아웃
            log.warn("[Scheduler] {} 연결 실패: {}", path, e.getMessage());
            throw new BusinessException(SchedulerErrorCode.SCHEDULER_UNAVAILABLE);
        }
    }
}
