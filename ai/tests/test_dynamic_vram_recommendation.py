"""추천 흐름이 확정된 학습 설정으로 VRAM 요구량을 정하고, 그 값으로 거르고, 응답하는지.

요구량 표 자체는 ``test_vram_requirement.py`` 가 본다. 여기서 볼 것은 배선이다 --
AUTO/CUSTOM으로 확정된 설정이 표에 닿는지, 거르기가 그 값을 쓰는지, 응답에 실리는지,
그리고 6 GiB / 8 GiB 경계.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.dataset import DatasetMetadata
from app.schemas.recommendation import RecommendationRequest
from app.schemas.training import TrainingMode
from app.scheduler.auto_config import build_auto_config
from app.scheduler.feasibility import NoFeasibleGpuError
from app.scheduler.recommendation import recommend_worker
from app.scheduler.vram_requirement import (
    QWEN3_0_6B_MODEL_ID,
    QWEN3_1_7B_MODEL_ID,
    VramRequirementSource,
)

Q06 = QWEN3_0_6B_MODEL_ID
Q17 = QWEN3_1_7B_MODEL_ID
LEGACY = {"minVramMb": 8192}

#: 6 GiB 카드(RTX 4050 Laptop급)와 8 GiB 카드(드라이버 보고값 8188).
#: gpuModel은 예측 모델이 아는 이름을 쓴다 -- 여기서 볼 것은 VRAM 판정이다.
GPU_6GB = {"workerId": "worker-6gb", "gpuModel": "NVIDIA GeForce RTX 4060",
           "vramMb": 6144, "benchmarkTokensPerSec": 20000.0}
GPU_8GB = {"workerId": "worker-8gb", "gpuModel": "NVIDIA GeForce RTX 4070 Laptop GPU",
           "vramMb": 8188, "benchmarkTokensPerSec": 29048.535}
GPU_24GB = {"workerId": "worker-24gb", "gpuModel": "NVIDIA GeForce RTX 4090",
            "vramMb": 24564, "benchmarkTokensPerSec": 72000.0}


def dataset(max_seq_length, sample_count=1440):
    return {"datasetSizeBytes": 462148, "sampleCount": sample_count,
            "totalTokens": 117561, "effectiveTokens": 154848,
            "avgSeqLength": 81.6, "maxSeqLength": max_seq_length}


def custom(batch_size, max_length):
    return {"epochs": 1, "batchSize": batch_size, "learningRate": 0.0002,
            "maxLength": max_length}


def request(model_id, training, max_seq_length, *candidates, requirements=LEGACY):
    return RecommendationRequest(
        model_id=model_id, docker_image_id=1,
        dataset=dataset(max_seq_length), training_config=training,
        requirements=requirements,
        gpu_candidates=list(candidates),
    )


class Predictor:
    """예측기 대역. 후보마다 같은 시간 -- 순위는 workerId로 갈린다."""

    def __init__(self):
        self.rows = []

    def predict(self, rows):
        self.rows = list(rows)
        return tuple(100.0 for _ in self.rows)


def surviving(model_id, training, max_seq_length, *candidates, requirements=LEGACY):
    predictor = Predictor()
    chosen = recommend_worker(
        request(model_id, training, max_seq_length, *candidates, requirements=requirements),
        predictor)
    return chosen, {row.as_mapping()["vram_mb"] for row in predictor.rows}


class TestCustomConfigs:
    def test_06b_b4_short_lets_a_6gb_gpu_in(self):
        chosen, seen = surviving(Q06, custom(4, 128), 127, GPU_6GB)

        assert chosen.required_vram_mb == 4608
        assert chosen.vram_requirement.source is VramRequirementSource.CALIBRATED_RULE
        assert seen == {6144}

    def test_06b_b8_short_keeps_a_6gb_gpu_out(self):
        with pytest.raises(NoFeasibleGpuError) as raised:
            surviving(Q06, custom(8, 128), 127, GPU_6GB)

        assert raised.value.min_vram_mb == 7168

    def test_06b_b8_short_fits_an_8gb_gpu(self):
        chosen, seen = surviving(Q06, custom(8, 128), 127, GPU_6GB, GPU_8GB)

        assert chosen.required_vram_mb == 7168
        assert seen == {8188}

    def test_06b_b4_long_keeps_an_8gb_gpu_out(self):
        with pytest.raises(NoFeasibleGpuError) as raised:
            surviving(Q06, custom(4, 512), 511, GPU_8GB)

        assert raised.value.min_vram_mb == 13312

    def test_06b_b4_long_goes_to_the_24gb_gpu(self):
        chosen, seen = surviving(Q06, custom(4, 512), 511, GPU_6GB, GPU_8GB, GPU_24GB)

        assert chosen.required_vram_mb == 13312
        assert seen == {24564}

    def test_17b_b4_short_fits_an_8gb_gpu(self):
        chosen, seen = surviving(Q17, custom(4, 128), 127, GPU_6GB, GPU_8GB)

        assert chosen.required_vram_mb == 7168
        assert seen == {8188}

    def test_17b_b8_short_keeps_an_8gb_gpu_out(self):
        with pytest.raises(NoFeasibleGpuError) as raised:
            surviving(Q17, custom(8, 128), 127, GPU_8GB)

        assert raised.value.min_vram_mb == 11264

    def test_the_effective_length_is_the_shorter_of_the_two(self):
        # maxLength 1024라도 가장 긴 샘플이 120이면 B4/L128 칸이다.
        chosen, _ = surviving(Q06, custom(4, 1024), 120, GPU_6GB)

        assert chosen.required_vram_mb == 4608


class TestAutoConfigs:
    """AUTO가 고른 설정 그대로 요구량이 정해진다. AUTO 규칙은 건드리지 않는다."""

    @staticmethod
    def auto(mode, max_seq_length, sample_count=1440):
        return build_auto_config(
            mode, DatasetMetadata(**{k: v for k, v in dataset(
                max_seq_length, sample_count).items() if k != "effectiveTokens"}))

    def test_training_mode_on_short_data_is_b4_and_fits_6gb(self):
        config = self.auto(TrainingMode.TRAINING, 120)
        assert (config.batch_size, config.max_length) == (4, 120)

        chosen, seen = surviving(Q06, config, 120, GPU_6GB)

        assert chosen.required_vram_mb == 4608
        assert seen == {6144}

    def test_balanced_on_short_data_is_b8_and_needs_more_than_6gb(self):
        config = self.auto(TrainingMode.BALANCED, 120)
        assert (config.batch_size, config.max_length) == (8, 120)

        chosen, seen = surviving(Q06, config, 120, GPU_6GB, GPU_8GB)

        assert chosen.required_vram_mb == 7168
        assert seen == {8188}

    def test_speed_is_b16_which_was_not_measured(self):
        config = self.auto(TrainingMode.SPEED, 120)
        assert config.batch_size == 16

        chosen, _ = surviving(Q06, config, 120, GPU_8GB)

        assert chosen.required_vram_mb == 8192
        assert chosen.vram_requirement.source is VramRequirementSource.LEGACY_FALLBACK

    def test_a_batch_capped_by_a_tiny_dataset_is_what_gets_estimated(self):
        # 샘플이 3개면 AUTO는 batch 3을 고른다 -- 표는 그 3으로 칸을 고른다.
        config = self.auto(TrainingMode.BALANCED, 120, sample_count=3)
        assert config.batch_size == 3

        chosen, _ = surviving(Q06, config, 120, GPU_6GB)

        assert chosen.required_vram_mb == 4608


class TestLegacyFallback:
    @pytest.mark.parametrize(("model_id", "training", "max_seq"), [
        (Q06, custom(16, 128), 127),     # 잰 적 없는 batch
        (Q06, custom(8, 512), 511),      # 잰 적 없는 batch·길이 조합
        (Q17, custom(4, 1024), 1000),    # 잰 적 없는 길이
        (1, custom(4, 128), 127),        # 표에 없는 모델
    ])
    def test_an_unmeasured_request_behaves_as_before(self, model_id, training, max_seq):
        chosen, seen = surviving(model_id, training, max_seq, GPU_6GB, GPU_8GB)

        assert chosen.required_vram_mb == 8192
        assert chosen.vram_requirement.source is VramRequirementSource.LEGACY_FALLBACK
        assert seen == {8188}   # 8188 → 공칭 8192, 기존 규칙 그대로

    def test_the_legacy_value_is_whatever_backend_sent(self):
        chosen, seen = surviving(Q06, custom(16, 128), 127, GPU_6GB,
                                 requirements={"minVramMb": 6000})

        assert chosen.required_vram_mb == 6000
        assert seen == {6144}

    def test_a_calibrated_cell_is_not_raised_to_the_legacy_value(self):
        chosen, _ = surviving(Q06, custom(4, 128), 127, GPU_6GB,
                              requirements={"minVramMb": 8192})

        assert chosen.required_vram_mb == 4608


@pytest.fixture
def client(trained_models_directory, monkeypatch):
    monkeypatch.setenv("GSHARED_SCHEDULER_MODELS_DIR", str(trained_models_directory))
    with TestClient(app) as client:
        yield client


def body(model_id, training, max_seq_length, *candidates, requirements=LEGACY):
    return {"modelId": model_id, "dockerImageId": 1, "dataset": dataset(max_seq_length),
            "trainingConfig": training, "requirements": requirements,
            "gpuCandidates": list(candidates)}


class TestTheResponse:
    def test_required_vram_is_in_the_response(self, client):
        response = client.post("/scheduler/recommend",
                               json=body(Q06, custom(4, 128), 127, GPU_6GB, GPU_8GB))

        assert response.status_code == 200
        answer = response.json()
        assert answer["requiredVramMb"] == 4608
        assert {entry["workerId"] for entry in answer["rankings"]} == {
            "worker-6gb", "worker-8gb"}

    def test_the_response_carries_the_legacy_value_when_unmeasured(self, client):
        answer = client.post("/scheduler/recommend",
                             json=body(Q06, custom(16, 128), 127, GPU_6GB, GPU_8GB)).json()

        assert answer["requiredVramMb"] == 8192
        assert [entry["workerId"] for entry in answer["rankings"]] == ["worker-8gb"]

    def test_no_feasible_gpu_is_still_409(self, client):
        response = client.post("/scheduler/recommend",
                               json=body(Q17, custom(8, 128), 127, GPU_6GB, GPU_8GB))

        assert response.status_code == 409
        assert "11264" in response.json()["detail"][0]["msg"]

    def test_the_schema_names_the_unit(self, client):
        schema = client.get("/openapi.json").json()["components"]["schemas"]
        field = schema["RecommendationResponse"]["properties"]["requiredVramMb"]

        assert field["type"] == "integer"
        assert "MiB" in field["description"]
        assert "requiredVramMb" in schema["RecommendationResponse"]["required"]
