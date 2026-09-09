"""API 경계: 실제 HTTP로 넣고, Backend가 받게 될 모양을 고정한다.

Ranking이 응답에 섞이지 않는다는 것도 여기서 못 박는다. 지금 안 넣는 것은 다음
작업(gpu-ranking)의 책임이기 때문이고, 한 번 섞여 나가면 Backend가 그것을 쓰기
시작해서 되돌리기 어려워진다.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.scheduler.runtime_model import LoadedRuntimeModel
from app.training.runtime_trainer import MODEL_VERSION

DATASET = {
    "datasetSizeBytes": 52428800,
    "sampleCount": 10000,
    "totalTokens": 3500000,
    "effectiveTokens": 2874100,
    "avgSeqLength": 350.0,
    "maxSeqLength": 1800,
}
TRAINING = {"epochs": 3, "batchSize": 8, "learningRate": 0.0002, "maxLength": 1024}
SLOW = {
    "workerId": "worker-slow",
    "gpuModel": "NVIDIA GeForce RTX 3060",
    "vramMb": 12288,
    "benchmarkTokensPerSec": 17000.0,
}
FAST = {
    "workerId": "worker-fast",
    "gpuModel": "NVIDIA GeForce RTX 4090",
    "vramMb": 24564,
    "benchmarkTokensPerSec": 72000.0,
}

PATH = "/scheduler/runtime/predict"


def body(**overrides):
    payload = {
        "modelId": 3,
        "dockerImageId": 1,
        "dataset": DATASET,
        "trainingConfig": TRAINING,
        "gpuCandidates": [FAST],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def client(trained_models_directory, monkeypatch):
    """모델이 준비된 앱. 시작할 때 테스트용 모델을 읽는다."""
    monkeypatch.setenv("GSHARED_SCHEDULER_MODELS_DIR", str(trained_models_directory))
    with TestClient(app) as client:
        yield client


class TestAValidRequest:
    def test_one_gpu_is_answered(self, client):
        response = client.post(PATH, json=body())

        assert response.status_code == 200
        assert len(response.json()["predictions"]) == 1

    def test_several_gpus_are_answered_in_one_request(self, client):
        # Backend가 후보마다 왕복하지 않도록.
        response = client.post(PATH, json=body(gpuCandidates=[SLOW, FAST]))

        assert len(response.json()["predictions"]) == 2

    def test_each_answer_names_the_gpu_it_is_about(self, client):
        answers = client.post(PATH, json=body(gpuCandidates=[SLOW, FAST])).json()

        assert [p["workerId"] for p in answers["predictions"]] == [
            "worker-slow", "worker-fast"]

    def test_the_order_follows_the_request(self, client):
        reversed_answers = client.post(
            PATH, json=body(gpuCandidates=[FAST, SLOW])).json()

        assert [p["workerId"] for p in reversed_answers["predictions"]] == [
            "worker-fast", "worker-slow"]

    def test_every_prediction_is_a_positive_number_of_seconds(self, client):
        answers = client.post(PATH, json=body(gpuCandidates=[SLOW, FAST])).json()

        for prediction in answers["predictions"]:
            assert prediction["predictedRuntimeSec"] > 0

    def test_the_answer_says_which_model_made_it(self, client):
        answers = client.post(PATH, json=body()).json()

        assert answers["schedulerModelVersion"] == MODEL_VERSION


class TestWhatTheAnswerDoesNotContain:
    """이번 작업은 순위를 매기지 않는다. 그것은 gpu-ranking의 책임이다."""

    def test_there_is_no_recommended_worker(self, client):
        answers = client.post(PATH, json=body(gpuCandidates=[SLOW, FAST])).json()

        assert "recommendedWorkerId" not in answers

    def test_there_is_no_rank_on_any_prediction(self, client):
        answers = client.post(PATH, json=body(gpuCandidates=[SLOW, FAST])).json()

        for prediction in answers["predictions"]:
            assert set(prediction) == {"workerId", "predictedRuntimeSec"}

    def test_nothing_about_cost_or_billing(self, client):
        # 비용은 Backend가 계산한다. Scheduler는 시간만 예측한다.
        text = client.post(PATH, json=body()).text

        for money in ("processingAmount", "cost", "price", "amount", "token_price"):
            assert money not in text

    def test_the_answers_are_not_sorted_for_us(self, client):
        # 느린 GPU를 먼저 넣으면 먼저 나온다. 정렬은 하지 않는다.
        answers = client.post(PATH, json=body(gpuCandidates=[SLOW, FAST])).json()
        seconds = [p["predictedRuntimeSec"] for p in answers["predictions"]]

        assert seconds == sorted(seconds, reverse=True)


class TestRequestsThatAreRefused:
    def test_no_gpu_candidates(self, client):
        assert client.post(PATH, json=body(gpuCandidates=[])).status_code == 422

    def test_a_dataset_without_effective_tokens(self, client):
        without = {k: v for k, v in DATASET.items() if k != "effectiveTokens"}

        assert client.post(PATH, json=body(dataset=without)).status_code == 422

    def test_a_dataset_whose_average_exceeds_its_maximum(self, client):
        broken = dict(DATASET, avgSeqLength=5000.0)

        assert client.post(PATH, json=body(dataset=broken)).status_code == 422

    def test_a_training_config_with_zero_epochs(self, client):
        assert client.post(
            PATH, json=body(trainingConfig=dict(TRAINING, epochs=0))).status_code == 422

    def test_a_gpu_with_no_benchmark(self, client):
        broken = dict(FAST, benchmarkTokensPerSec=0.0)

        assert client.post(PATH, json=body(gpuCandidates=[broken])).status_code == 422

    def test_a_gpu_with_an_infinite_benchmark(self, client):
        """``inf > 0`` 은 참이라 ``gt=0`` 만으로는 통과한다.

        표준 JSON에 ``Infinity`` 리터럴은 없지만 파이썬의 ``json.loads`` 는 그것을
        받아들이므로, 그렇게 만든 본문은 서버까지 도달한다. 그래서 문자열을 직접
        만들어 보낸다 -- 클라이언트가 직렬화해 주기를 기다리면 이 경로는 영영
        테스트되지 않는다.
        """
        import json

        raw = json.dumps(body(gpuCandidates=[dict(FAST, benchmarkTokensPerSec=1.0)]))
        raw = raw.replace('"benchmarkTokensPerSec": 1.0', '"benchmarkTokensPerSec": Infinity')

        response = client.post(
            PATH, content=raw, headers={"Content-Type": "application/json"})

        assert response.status_code == 422
        # 500이 아니어야 한다. 값을 제대로 거절해 놓고 응답을 만들다 터지면
        # Backend에게는 서버 잘못으로 보인다.
        assert "inf" in str(response.json()["detail"]).lower()

    def test_a_gpu_with_a_nan_benchmark(self, client):
        import json

        raw = json.dumps(body(gpuCandidates=[dict(FAST, benchmarkTokensPerSec=1.0)]))
        raw = raw.replace('"benchmarkTokensPerSec": 1.0', '"benchmarkTokensPerSec": NaN')

        response = client.post(
            PATH, content=raw, headers={"Content-Type": "application/json"})

        assert response.status_code == 422

    def test_an_unknown_gpu_model_is_refused_rather_than_guessed(self, client):
        unknown = dict(FAST, gpuModel="A GPU From The Future")

        response = client.post(PATH, json=body(gpuCandidates=[unknown]))

        assert response.status_code == 422
        assert response.json()["detail"][0]["type"] == "unknown_category"

    def test_an_unknown_base_model_is_refused(self, client):
        response = client.post(PATH, json=body(modelId=9999))

        assert response.status_code == 422

    def test_a_field_that_belongs_to_the_next_branch_is_refused(self, client):
        assert client.post(
            PATH, json=body(recommendedWorkerId="worker-fast")).status_code == 422


class TestWhenTheModelIsNotThere:
    def test_it_says_so_rather_than_making_a_number_up(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GSHARED_SCHEDULER_MODELS_DIR", str(tmp_path))

        with TestClient(app) as client:
            response = client.post(PATH, json=body())

        assert response.status_code == 503
        assert "no runtime model" in response.json()["detail"]

    def test_the_rest_of_the_scheduler_still_works(self, tmp_path, monkeypatch):
        # Runtime 모델이 없다고 AUTO와 input까지 죽을 이유가 없다.
        monkeypatch.setenv("GSHARED_SCHEDULER_MODELS_DIR", str(tmp_path))

        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            assert client.post("/scheduler/input", json={
                "dataset": {k: v for k, v in DATASET.items() if k != "effectiveTokens"},
                "training": TRAINING,
            }).status_code == 200

    def test_a_model_that_could_not_be_loaded_is_not_replaced_by_a_fake(self, tmp_path):
        loaded = LoadedRuntimeModel.load_from(tmp_path)

        assert loaded.predictor is None
