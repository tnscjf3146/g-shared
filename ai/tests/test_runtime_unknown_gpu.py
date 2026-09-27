"""학습 때 없던 GPU 이름이 추천 전체를 멈추지 않는지, 그리고 아는 GPU는 그대로인지.

모르는 이름은 그 Worker가 잰 benchmark와 가장 가까운 학습 카테고리로만 예측한다.
이름으로 고르지 않는다 -- 아래 테스트에 "4050이면 4060" 같은 가정은 없다.
"""

import json
import math
import shutil

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.runtime import GpuCandidate, RuntimeDatasetMetadata
from app.schemas.training import TrainingConfig
from app.scheduler.runtime_features import UnknownCategoryError, build_runtime_features
from app.scheduler.runtime_predictor import (
    GpuCategorySource,
    load_runtime_predictor,
)
from app.training.runtime_dataset import synthetic_benchmark_profiles
from app.training.runtime_trainer import METADATA_FILE_NAME, MODEL_FILE_NAME

DIRECT = GpuCategorySource.DIRECT
PROXY = GpuCategorySource.BENCHMARK_PROXY

RTX_4050_LAPTOP = "NVIDIA GeForce RTX 4050 Laptop GPU"
RTX_5070_LAPTOP = "NVIDIA GeForce RTX 5070 Laptop GPU"
RTX_4060 = "NVIDIA GeForce RTX 4060"
RTX_4070_LAPTOP = "NVIDIA GeForce RTX 4070 Laptop GPU"

DATASET = RuntimeDatasetMetadata(
    dataset_size_bytes=462148, sample_count=1440, total_tokens=117561,
    effective_tokens=154848, avg_seq_length=81.6, max_seq_length=123)
TRAINING = TrainingConfig(epochs=1, batch_size=4, learning_rate=0.0002, max_length=128)


def row(gpu_model, benchmark, vram=8188, model_id=2):
    return build_runtime_features(
        dataset=DATASET, training=TRAINING, model_id=model_id,
        candidate=GpuCandidate(worker_id="w", gpu_model=gpu_model, vram_mb=vram,
                               benchmark_tokens_per_sec=benchmark))


@pytest.fixture
def directory(tmp_path, trained_models_directory):
    for name in (MODEL_FILE_NAME, METADATA_FILE_NAME):
        shutil.copy(trained_models_directory / name, tmp_path / name)
    return tmp_path


def rewrite_metadata(directory, **changes):
    path = directory / METADATA_FILE_NAME
    metadata = json.loads(path.read_text(encoding="utf-8"))
    for key, value in changes.items():
        if value is None:
            metadata.pop(key, None)
        else:
            metadata[key] = value
    path.write_text(json.dumps(metadata), encoding="utf-8")


def profiles_of(predictor):
    return dict(predictor.gpu_benchmark_profiles)


class TestKnownGpus:
    @pytest.mark.parametrize("name", [RTX_4070_LAPTOP, RTX_4060])
    def test_a_known_name_is_used_directly(self, predictor, name):
        resolved = predictor.resolve_gpu_category(name, 29048.535)

        assert (resolved.category, resolved.source) == (name, DIRECT)

    def test_a_known_name_is_not_remapped_by_an_odd_benchmark(self, predictor):
        # 4060 이름에 4090급 benchmark가 붙어도 이름이 우선이다.
        resolved = predictor.resolve_gpu_category(RTX_4060, 72000.0)

        assert (resolved.category, resolved.source) == (RTX_4060, DIRECT)

    def test_known_predictions_do_not_change(self, predictor, directory):
        # 대표값이 없는 예측기(= 이 변경 전과 같은 경로)와 숫자가 같아야 한다.
        rows = [row(RTX_4070_LAPTOP, 29048.535), row(RTX_4060, 21000.0, vram=8188)]
        rewrite_metadata(directory, gpuBenchmarkProfiles={}, trainingDataType="measured")
        without_profiles = load_runtime_predictor(directory)
        assert not without_profiles.gpu_benchmark_profiles

        assert predictor.predict(rows) == without_profiles.predict(rows)


class TestUnknownGpus:
    def test_rtx_4050_laptop_is_predicted_through_a_benchmark_proxy(self, predictor):
        # 테스트용 benchmark 값이다(실측 아님). 4060과 3060 대표값 사이 어딘가.
        rows = [row(RTX_4050_LAPTOP, 19500.0, vram=6144)]

        resolved = predictor.resolve_gpu_category(RTX_4050_LAPTOP, 19500.0)
        (seconds,) = predictor.predict(rows)

        assert resolved.source is PROXY
        assert resolved.category in predictor.categories.known_values("gpu_model")
        assert math.isfinite(seconds) and seconds > 0

    def test_the_proxy_follows_the_benchmark_not_the_name(self, predictor):
        profiles = profiles_of(predictor)
        slowest = min(profiles, key=profiles.get)
        fastest = max(profiles, key=profiles.get)

        assert predictor.resolve_gpu_category(RTX_4050_LAPTOP, profiles[slowest]).category == slowest
        assert predictor.resolve_gpu_category(RTX_4050_LAPTOP, profiles[fastest]).category == fastest

    def test_a_brand_new_name_is_handled_the_same_way(self, predictor):
        resolved = predictor.resolve_gpu_category(RTX_5070_LAPTOP, 45000.0)
        (seconds,) = predictor.predict([row(RTX_5070_LAPTOP, 45000.0, vram=8188)])

        assert resolved.source is PROXY
        assert math.isfinite(seconds) and seconds > 0

    def test_the_request_row_keeps_its_real_name(self, predictor):
        features = row(RTX_4050_LAPTOP, 19500.0, vram=6144)
        predictor.predict([features])

        assert features.gpu_model == RTX_4050_LAPTOP

    def test_the_actual_benchmark_and_vram_are_used(self, predictor):
        # proxy는 카테고리 한 칸만 바꾼다. benchmark 열은 Worker 값 그대로라,
        # 같은 proxy여도 benchmark가 다르면 예측이 달라질 수 있어야 한다.
        profiles = profiles_of(predictor)
        name = sorted(profiles, key=profiles.get)[2]
        low, high = profiles[name] * 0.97, profiles[name] * 1.03
        assert predictor.resolve_gpu_category(RTX_5070_LAPTOP, low).category == name
        assert predictor.resolve_gpu_category(RTX_5070_LAPTOP, high).category == name

        direct = predictor.predict([row(name, low), row(name, high)])
        proxied = predictor.predict([row(RTX_5070_LAPTOP, low), row(RTX_5070_LAPTOP, high)])

        assert proxied == direct


class TestNearestRule:
    def test_an_exact_profile_match_picks_that_category(self, predictor):
        for name, value in profiles_of(predictor).items():
            resolved = predictor.resolve_gpu_category(RTX_5070_LAPTOP, value)
            assert (resolved.category, resolved.source) == (name, PROXY)

    def test_between_two_profiles_the_closer_ratio_wins(self, predictor):
        profiles = profiles_of(predictor)
        a, b = sorted(profiles, key=profiles.get)[:2]
        # 기하평균보다 살짝 아래/위
        middle = math.sqrt(profiles[a] * profiles[b])

        assert predictor.resolve_gpu_category(RTX_5070_LAPTOP, middle * 0.99).category == a
        assert predictor.resolve_gpu_category(RTX_5070_LAPTOP, middle * 1.01).category == b

    def test_an_exact_tie_goes_to_the_slower_category(self, directory):
        rewrite_metadata(directory, gpuBenchmarkProfiles={
            RTX_4060: 20000.0, RTX_4070_LAPTOP: 45000.0})
        predictor = load_runtime_predictor(directory)
        # 기하평균 30000은 두 대표값과의 비율 거리가 같다.
        resolved = predictor.resolve_gpu_category(RTX_5070_LAPTOP, 30000.0)

        assert resolved.category == RTX_4060

    def test_far_above_every_profile_picks_the_fastest(self, predictor):
        profiles = profiles_of(predictor)

        assert predictor.resolve_gpu_category(RTX_5070_LAPTOP, 1e7).category == max(
            profiles, key=profiles.get)


class TestNoGroundsForAProxy:
    @pytest.mark.parametrize("benchmark", [None, 0, 0.0, -1.0, float("nan"),
                                           float("inf"), True, "19500"])
    def test_an_unusable_benchmark_is_not_mapped(self, predictor, benchmark):
        resolved = predictor.resolve_gpu_category(RTX_4050_LAPTOP, benchmark)

        assert (resolved.category, resolved.source) == (RTX_4050_LAPTOP, DIRECT)

    def test_without_profiles_an_unknown_gpu_is_refused_as_before(self, directory):
        rewrite_metadata(directory, gpuBenchmarkProfiles=None, trainingDataType="measured")
        predictor = load_runtime_predictor(directory)

        with pytest.raises(UnknownCategoryError):
            predictor.predict([row(RTX_4050_LAPTOP, 19500.0, vram=6144)])


class TestWhereTheProfilesComeFrom:
    def test_a_trained_model_stores_them_in_metadata(self, trained_models_directory):
        metadata = json.loads(
            (trained_models_directory / METADATA_FILE_NAME).read_text(encoding="utf-8"))
        profiles = metadata["gpuBenchmarkProfiles"]

        assert set(profiles) == set(metadata["categoryMappings"]["gpu_model"])
        assert all(value > 0 for value in profiles.values())

    def test_a_synthetic_model_without_the_key_uses_the_generator(self, directory):
        rewrite_metadata(directory, gpuBenchmarkProfiles=None,
                         trainingDataType="synthetic-bootstrap")

        assert profiles_of(load_runtime_predictor(directory)) == synthetic_benchmark_profiles()

    def test_the_generator_profile_is_the_middle_of_each_range(self):
        profiles = synthetic_benchmark_profiles()

        assert profiles[RTX_4070_LAPTOP] == (28_300 + 30_000) / 2

    def test_bad_stored_values_are_dropped(self, directory):
        rewrite_metadata(directory, gpuBenchmarkProfiles={RTX_4060: -5, RTX_4070_LAPTOP: 29000})

        assert profiles_of(load_runtime_predictor(directory)) == {RTX_4070_LAPTOP: 29000.0}


@pytest.fixture
def client(trained_models_directory, monkeypatch):
    monkeypatch.setenv("GSHARED_SCHEDULER_MODELS_DIR", str(trained_models_directory))
    with TestClient(app) as client:
        yield client


def recommend_body(*candidates, min_vram=6000):
    return {"modelId": 2, "dockerImageId": 1,
            "dataset": {"datasetSizeBytes": 462148, "sampleCount": 1440, "totalTokens": 117561,
                        "effectiveTokens": 154848, "avgSeqLength": 81.6, "maxSeqLength": 123},
            "trainingConfig": {"epochs": 1, "batchSize": 4, "learningRate": 0.0002, "maxLength": 128},
            "requirements": {"minVramMb": min_vram},
            "gpuCandidates": list(candidates)}


GPU_4050 = {"workerId": "worker-4050", "gpuModel": RTX_4050_LAPTOP,
            "vramMb": 6144, "benchmarkTokensPerSec": 19500.0}
GPU_4070 = {"workerId": "worker-4070", "gpuModel": RTX_4070_LAPTOP,
            "vramMb": 8188, "benchmarkTokensPerSec": 29048.535}


class TestTheRecommendationFlow:
    def test_an_unknown_gpu_is_ranked_instead_of_refused(self, client):
        response = client.post("/scheduler/recommend", json=recommend_body(GPU_4050, GPU_4070))

        assert response.status_code == 200
        answer = response.json()
        assert {entry["workerId"] for entry in answer["rankings"]} == {"worker-4050", "worker-4070"}
        assert all(entry["predictedRuntimeSec"] > 0 for entry in answer["rankings"])

    def test_the_unknown_gpu_alone_is_recommended(self, client):
        answer = client.post("/scheduler/recommend", json=recommend_body(GPU_4050)).json()

        assert answer["recommendedWorkerId"] == "worker-4050"

    def test_runtime_predict_answers_too(self, client):
        body = recommend_body(GPU_4050)
        body.pop("requirements")
        response = client.post("/scheduler/runtime/predict", json=body)

        assert response.status_code == 200

    @pytest.mark.parametrize("benchmark", [None, 0, -1.0])
    def test_a_bad_benchmark_is_still_a_422_validation_error(self, client, benchmark):
        response = client.post("/scheduler/recommend", json=recommend_body(
            dict(GPU_4050, benchmarkTokensPerSec=benchmark)))

        assert response.status_code == 422
        assert all(item["type"] != "unknown_category" for item in response.json()["detail"])
