package com.gshared.back.global.client;

import com.gshared.back.global.client.dto.SchedulerRecommendRequest;
import com.gshared.back.global.client.dto.SchedulerTrainingConfig;
import com.gshared.back.global.exception.BusinessException;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * AI Scheduler 의 오류 응답을 Backend 에러 코드로 옮기는 규칙.
 *
 * <p>AI 는 실패 이유를 상태 코드와 {@code detail[].type} 으로 정확히 알려 준다(ai/docs/scheduler-api-contract.md).
 * 그 이유를 버리면 사용자는 무엇을 바꿔야 할지 모르는 문구를 보게 된다.
 * 목(mock)이 아니라 실제 HTTP 로 응답을 받아, RestClient 가 던지는 예외까지 그대로 거친다.
 */
class SchedulerClientTest {

    private static final String RECOMMEND = "/scheduler/recommend";

    private HttpServer server;

    @AfterEach
    void stop() {
        if (server != null) {
            server.stop(0);
        }
    }

    @Test
    @DisplayName("409 no_feasible_gpu 는 NO_FEASIBLE_GPU — 후보 GPU 로는 안 돌아간다는 뜻을 살린다")
    void conflict_no_feasible_gpu_maps_to_no_feasible_gpu() throws IOException {
        SchedulerClient client = clientRespondingWith(409, """
                {"detail":[{"type":"no_feasible_gpu","loc":["body","gpuCandidates"],
                "msg":"none of the 3 GPU candidates has at least 40960 MiB of VRAM, which this job requires"}]}
                """);

        assertThatThrownBy(() -> client.recommend(anyRequest()))
                .isInstanceOf(BusinessException.class)
                .extracting("errorCode")
                .isEqualTo(SchedulerErrorCode.NO_FEASIBLE_GPU);
    }

    @Test
    @DisplayName("본문에 no_feasible_gpu 가 없는 409 는 SCHEDULER_REJECTED — 모르는 409 를 추측해 매핑하지 않는다")
    void conflict_without_known_type_stays_rejected() throws IOException {
        SchedulerClient client = clientRespondingWith(409, """
                {"detail":[{"type":"something_new","loc":["body"],"msg":"?"}]}
                """);

        assertThatThrownBy(() -> client.recommend(anyRequest()))
                .isInstanceOf(BusinessException.class)
                .extracting("errorCode")
                .isEqualTo(SchedulerErrorCode.SCHEDULER_REJECTED);
    }

    @Test
    @DisplayName("422 unknown_category 는 그대로 SCHEDULER_REJECTED")
    void unprocessable_stays_rejected() throws IOException {
        SchedulerClient client = clientRespondingWith(422, """
                {"detail":[{"type":"unknown_category","loc":["body","modelId"],"msg":"unknown model"}]}
                """);

        assertThatThrownBy(() -> client.recommend(anyRequest()))
                .isInstanceOf(BusinessException.class)
                .extracting("errorCode")
                .isEqualTo(SchedulerErrorCode.SCHEDULER_REJECTED);
    }

    @Test
    @DisplayName("503 은 그대로 SCHEDULER_UNAVAILABLE")
    void service_unavailable_stays_unavailable() throws IOException {
        SchedulerClient client = clientRespondingWith(503, """
                {"detail":"predictor not loaded"}
                """);

        assertThatThrownBy(() -> client.recommend(anyRequest()))
                .isInstanceOf(BusinessException.class)
                .extracting("errorCode")
                .isEqualTo(SchedulerErrorCode.SCHEDULER_UNAVAILABLE);
    }

    private SchedulerClient clientRespondingWith(int status, String body) throws IOException {
        server = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        server.createContext(RECOMMEND, exchange -> {
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.getRequestBody().readAllBytes();
            exchange.getResponseHeaders().add("Content-Type", "application/json");
            exchange.sendResponseHeaders(status, bytes.length);
            try (OutputStream out = exchange.getResponseBody()) {
                out.write(bytes);
            }
        });
        server.start();

        String baseUrl = "http://127.0.0.1:" + server.getAddress().getPort();
        return new SchedulerClient(new SchedulerProperties(baseUrl, Duration.ofSeconds(2), Duration.ofSeconds(2)));
    }

    private static SchedulerRecommendRequest anyRequest() {
        return new SchedulerRecommendRequest(
                1L, 1L,
                new SchedulerRecommendRequest.DatasetMeta(1_000L, 10, 1_000L, 1_000L, 100.0, 128),
                new SchedulerTrainingConfig(3, 16, 0.00003, 512),
                new SchedulerRecommendRequest.Requirements(40_960),
                List.of(new SchedulerRecommendRequest.GpuCandidate("w-1", "RTX 4070 Laptop", 8_188, 1_000.0, null)));
    }
}
