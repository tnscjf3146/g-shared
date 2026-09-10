"""Runtime 예측 요청의 계약. 여기서 막지 못한 값은 그대로 예측 입력이 된다.

기존 스키마와 같은 규칙을 따른다 -- camelCase, 모르는 필드 거절, 숫자 문자열 거절.
이 파일이 그것을 다시 확인하는 이유는 새 스키마가 그 규칙 밖으로 새어 나가지
않았는지 고정하기 위해서다.
"""

import math

import pytest
from pydantic import ValidationError

from app.schemas.runtime import (
    GpuCandidate,
    RuntimeDatasetMetadata,
    RuntimePredictionRequest,
)

DATASET = {
    "datasetSizeBytes": 52428800,
    "sampleCount": 10000,
    "totalTokens": 3500000,
    "effectiveTokens": 2874100,
    "avgSeqLength": 350.0,
    "maxSeqLength": 1800,
}
TRAINING = {"epochs": 3, "batchSize": 8, "learningRate": 0.0002, "maxLength": 1024}
CANDIDATE = {
    "workerId": "worker-123",
    "gpuModel": "NVIDIA GeForce RTX 4070 Laptop GPU",
    "vramMb": 8188,
    "benchmarkTokensPerSec": 36521.7,
}


def request_payload(**overrides):
    payload = {
        "modelId": 3,
        "dockerImageId": 1,
        "dataset": DATASET,
        "trainingConfig": TRAINING,
        "gpuCandidates": [CANDIDATE],
    }
    payload.update(overrides)
    return payload


class TestAValidRequest:
    def test_it_is_accepted(self):
        assert RuntimePredictionRequest(**request_payload()) is not None

    def test_several_candidates_are_accepted(self):
        second = dict(CANDIDATE, workerId="worker-456", benchmarkTokensPerSec=70000.0)

        request = RuntimePredictionRequest(**request_payload(
            gpuCandidates=[CANDIDATE, second]))

        assert len(request.gpu_candidates) == 2

    def test_effective_tokens_is_read(self):
        request = RuntimePredictionRequest(**request_payload())

        assert request.dataset.effective_tokens == 2874100


class TestTheDataset:
    """기존 DatasetMetadata를 물려받았으므로 그 규칙도 그대로 살아 있어야 한다."""

    def test_the_inherited_average_rule_still_applies(self):
        with pytest.raises(ValidationError):
            RuntimeDatasetMetadata(**dict(DATASET, avgSeqLength=2000.0))

    @pytest.mark.parametrize("field", [
        "sampleCount", "effectiveTokens", "maxSeqLength", "datasetSizeBytes",
    ])
    def test_zero_is_refused(self, field):
        with pytest.raises(ValidationError):
            RuntimeDatasetMetadata(**dict(DATASET, **{field: 0}))

    @pytest.mark.parametrize("field", ["sampleCount", "effectiveTokens"])
    def test_a_negative_value_is_refused(self, field):
        with pytest.raises(ValidationError):
            RuntimeDatasetMetadata(**dict(DATASET, **{field: -1}))

    def test_effective_tokens_is_required(self):
        without = {key: value for key, value in DATASET.items()
                   if key != "effectiveTokens"}

        with pytest.raises(ValidationError):
            RuntimeDatasetMetadata(**without)

    def test_an_unknown_field_is_refused(self):
        with pytest.raises(ValidationError):
            RuntimeDatasetMetadata(**dict(DATASET, benchmarkScore=1234.0))


class TestAGpuCandidate:
    def test_zero_vram_is_refused(self):
        with pytest.raises(ValidationError):
            GpuCandidate(**dict(CANDIDATE, vramMb=0))

    def test_zero_benchmark_is_refused(self):
        # 초당 0토큰인 GPU는 어떤 작업도 끝내지 못한다.
        with pytest.raises(ValidationError):
            GpuCandidate(**dict(CANDIDATE, benchmarkTokensPerSec=0.0))

    def test_a_negative_benchmark_is_refused(self):
        with pytest.raises(ValidationError):
            GpuCandidate(**dict(CANDIDATE, benchmarkTokensPerSec=-1.0))

    def test_nan_is_refused(self):
        with pytest.raises(ValidationError):
            GpuCandidate(**dict(CANDIDATE, benchmarkTokensPerSec=math.nan))

    def test_infinity_is_refused(self):
        with pytest.raises(ValidationError):
            GpuCandidate(**dict(CANDIDATE, benchmarkTokensPerSec=math.inf))

    def test_a_numeric_string_is_refused(self):
        # strict 정책. JSON에 숫자 타입이 있으므로 문자열로 왔다면 보내는 쪽이 틀렸다.
        with pytest.raises(ValidationError):
            GpuCandidate(**dict(CANDIDATE, vramMb="8188"))

    def test_an_empty_worker_id_is_refused(self):
        with pytest.raises(ValidationError):
            GpuCandidate(**dict(CANDIDATE, workerId=""))

    def test_benchmark_score_is_not_part_of_the_contract(self):
        # 예전 System 계약의 이름. 지금 계약에는 없고, 보내면 거절된다.
        with pytest.raises(ValidationError):
            GpuCandidate(**dict(CANDIDATE, benchmarkScore=1234.0))


class TestTheRequestAsAWhole:
    def test_an_empty_candidate_list_is_refused(self):
        # 예측할 대상이 없는 요청이다.
        with pytest.raises(ValidationError):
            RuntimePredictionRequest(**request_payload(gpuCandidates=[]))

    def test_the_ids_are_database_keys(self):
        request = RuntimePredictionRequest(**request_payload(modelId=3, dockerImageId=1))

        assert request.model_id == 3
        assert request.docker_image_id == 1

    @pytest.mark.parametrize("field", ["modelId", "dockerImageId"])
    @pytest.mark.parametrize("bad", [0, -1])
    def test_an_id_that_cannot_be_a_primary_key_is_refused(self, field, bad):
        # 0이나 음수인 PK는 없다. 그런 값이 왔다면 보내는 쪽이 잘못 만든 것이다.
        with pytest.raises(ValidationError):
            RuntimePredictionRequest(**request_payload(**{field: bad}))

    @pytest.mark.parametrize("field", ["modelId", "dockerImageId"])
    def test_a_numeric_string_id_is_refused(self, field):
        # strict 정책. JSON에 숫자 타입이 있으므로 "3" 으로 왔다면 보내는 쪽이 틀렸다.
        with pytest.raises(ValidationError):
            RuntimePredictionRequest(**request_payload(**{field: "3"}))

    @pytest.mark.parametrize("field", ["modelId", "dockerImageId"])
    def test_a_missing_id_is_refused(self, field):
        payload = {k: v for k, v in request_payload().items() if k != field}

        with pytest.raises(ValidationError):
            RuntimePredictionRequest(**payload)

    def test_an_unknown_top_level_field_is_refused(self):
        with pytest.raises(ValidationError):
            RuntimePredictionRequest(**request_payload(recommendedWorkerId="worker-1"))

    def test_a_bad_training_config_is_refused(self):
        with pytest.raises(ValidationError):
            RuntimePredictionRequest(**request_payload(
                trainingConfig=dict(TRAINING, epochs=0)))
