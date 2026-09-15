package com.gshared.back.global.client.dto;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Backend → AI Scheduler 요청 JSON 이 AI 의 strict 스키마({@code extra="forbid"})와 맞는지.
 *
 * <p>AI 는 모르는 필드·빠진 필수 필드를 422 로 거절하고, 그쪽 스키마를 느슨하게 바꾸지 않는다.
 * 그러니 계약이 어긋나면 여기서 먼저 걸려야 한다 — 운영에서 AUTO 모드가 통째로 죽고 나서가 아니라.
 */
class SchedulerRequestContractTest {

    private final ObjectMapper objectMapper = new ObjectMapper();

    private static final SchedulerRecommendRequest.DatasetMeta RECOMMEND_META =
            new SchedulerRecommendRequest.DatasetMeta(52_428_800L, 10_000, 3_500_000L, 2_874_100L, 350.0, 1800);

    @Test
    @DisplayName("training-config 요청의 dataset 은 5개 필드뿐 — effectiveTokens 가 섞이면 AI 가 422")
    void training_config_dataset_has_no_effective_tokens() {
        JsonNode json = objectMapper.valueToTree(new SchedulerTrainingConfigRequest(
                SchedulerTrainingConfigRequest.DatasetMeta.from(RECOMMEND_META), "BALANCED"));

        JsonNode dataset = json.path("dataset");
        assertThat(dataset.size()).isEqualTo(5);
        assertThat(dataset.has("effectiveTokens")).isFalse();
        assertThat(dataset.path("datasetSizeBytes").asLong()).isEqualTo(52_428_800L);
        assertThat(dataset.path("sampleCount").asInt()).isEqualTo(10_000);
        assertThat(dataset.path("totalTokens").asLong()).isEqualTo(3_500_000L);
        assertThat(dataset.path("avgSeqLength").asDouble()).isEqualTo(350.0);
        assertThat(dataset.path("maxSeqLength").asInt()).isEqualTo(1800);
        assertThat(json.path("mode").asString()).isEqualTo("BALANCED");
    }

    @Test
    @DisplayName("recommend 요청은 requirements.minVramMb 를 싣고, dataset 에는 effectiveTokens 가 있다")
    void recommend_carries_requirements_and_effective_tokens() {
        JsonNode json = objectMapper.valueToTree(new SchedulerRecommendRequest(
                3L, 1L, RECOMMEND_META,
                new SchedulerTrainingConfig(3, 8, 0.0002, 1024),
                new SchedulerRecommendRequest.Requirements(8192),
                List.of(new SchedulerRecommendRequest.GpuCandidate("w-1", "RTX 4090", 24_564, 72_000.0))));

        assertThat(json.path("requirements").path("minVramMb").asInt()).isEqualTo(8192);
        assertThat(json.path("dataset").path("effectiveTokens").asLong()).isEqualTo(2_874_100L);
        assertThat(json.path("dataset").size()).isEqualTo(6);
    }
}
