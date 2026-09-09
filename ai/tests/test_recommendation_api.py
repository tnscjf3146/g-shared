"""API 경계: 실제 HTTP로 넣고, Backend가 받게 될 모양을 고정한다.

가격이 응답에 섞이지 않는다는 것도 여기서 못 박는다. Scheduler는 시간을 예측하고
하나를 고를 뿐이고, 얼마를 청구할지는 Backend가 정한다. 한 번 섞여 나가면
Backend가 그것을 쓰기 시작해서 되돌리기 어려워진다.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.training.runtime_trainer import MODEL_VERSION

DATASET = {
    "datasetSizeBytes": 52428800, "sampleCount": 10000, "totalTokens": 3500000,
    "effectiveTokens": 2874100, "avgSeqLength": 350.0, "maxSeqLength": 1800,
}
TRAINING = {"epochs": 3, "batchSize": 8, "learningRate": 0.0002, "maxLength": 1024}
SLOW = {"workerId": "worker-3060", "gpuModel": "NVIDIA GeForce RTX 3060",
        "vramMb": 12288, "benchmarkTokensPerSec": 17000.0}
MIDDLE = {"workerId": "worker-4070", "gpuModel": "NVIDIA GeForce RTX 4070 Laptop GPU",
          "vramMb": 8188, "benchmarkTokensPerSec": 36521.7}
FAST = {"workerId": "worker-4090", "gpuModel": "NVIDIA GeForce RTX 4090",
        "vramMb": 24564, "benchmarkTokensPerSec": 72000.0}

PATH = "/scheduler/recommend"
PREDICT_PATH = "/scheduler/runtime/predict"


def body(**overrides):
    payload = {
        "modelId": "qwen2.5-7b", "dockerImageId": "qlora-sft-v1",
        "dataset": DATASET, "trainingConfig": TRAINING,
        "gpuCandidates": [SLOW, MIDDLE, FAST],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def client(trained_models_directory, monkeypatch):
    monkeypatch.setenv("GSHARED_SCHEDULER_MODELS_DIR", str(trained_models_directory))
    with TestClient(app) as client:
        yield client


class TestAValidRequest:
    def test_one_candidate_is_answered(self, client):
        response = client.post(PATH, json=body(gpuCandidates=[MIDDLE]))

        assert response.status_code == 200
        assert response.json()["recommendedWorkerId"] == "worker-4070"

    def test_several_candidates_are_ranked(self, client):
        answer = client.post(PATH, json=body()).json()

        assert len(answer["rankings"]) == 3

    def test_the_rankings_are_ascending_by_predicted_seconds(self, client):
        answer = client.post(PATH, json=body()).json()

        seconds = [entry["predictedRuntimeSec"] for entry in answer["rankings"]]
        assert seconds == sorted(seconds)

    def test_rank_starts_at_one_and_has_no_gaps(self, client):
        answer = client.post(PATH, json=body()).json()

        assert [entry["rank"] for entry in answer["rankings"]] == [1, 2, 3]

    def test_the_recommendation_is_the_rank_one_worker(self, client):
        answer = client.post(PATH, json=body()).json()

        assert answer["recommendedWorkerId"] == answer["rankings"][0]["workerId"]

    def test_it_says_which_model_made_the_predictions(self, client):
        # 순위 매기기는 별도의 ML 모델이 아니므로 랭킹 버전을 따로 만들지 않는다.
        answer = client.post(PATH, json=body()).json()

        assert answer["schedulerModelVersion"] == MODEL_VERSION

    def test_the_input_order_does_not_change_the_answer(self, client):
        forwards = client.post(PATH, json=body()).json()
        backwards = client.post(
            PATH, json=body(gpuCandidates=[FAST, MIDDLE, SLOW])).json()

        assert forwards["recommendedWorkerId"] == backwards["recommendedWorkerId"]
        assert forwards["rankings"] == backwards["rankings"]

    def test_every_candidate_appears_once(self, client):
        answer = client.post(PATH, json=body()).json()

        assert sorted(e["workerId"] for e in answer["rankings"]) == [
            "worker-3060", "worker-4070", "worker-4090"]


class TestTheResponseShape:
    def test_each_entry_has_exactly_the_three_agreed_fields(self, client):
        answer = client.post(PATH, json=body()).json()

        for entry in answer["rankings"]:
            assert set(entry) == {"rank", "workerId", "predictedRuntimeSec"}

    def test_the_top_level_has_exactly_the_three_agreed_fields(self, client):
        answer = client.post(PATH, json=body()).json()

        assert set(answer) == {
            "schedulerModelVersion", "recommendedWorkerId", "rankings"}

    def test_nothing_about_money(self, client):
        # 비용은 Backend가 계산한다.
        text = client.post(PATH, json=body()).text

        for money in ("processingAmount", "estimatedCost", "cost", "price",
                      "tokenAmount", "amount"):
            assert money not in text

    def test_nothing_about_the_worker_beyond_its_id(self, client):
        text = client.post(PATH, json=body()).text

        for leaked in ("workerToken", "owner", "ONLINE", "OFFLINE", "vramMb",
                       "benchmarkTokensPerSec", "gpuModel"):
            assert leaked not in text

    def test_no_feature_vector_or_model_internals(self, client):
        # schedulerModelVersion 안의 "xgb"는 계약이 요구하는 값이므로 제외한다.
        text = client.post(PATH, json=body()).text

        for internal in ("effectiveTokens", "featureNames", "categoryMappings",
                         "booster", "n_estimators", "tree_method"):
            assert internal not in text

    def test_the_seconds_are_not_rounded_to_whole_numbers(self, client):
        answer = client.post(PATH, json=body()).json()

        assert any(entry["predictedRuntimeSec"] % 1 for entry in answer["rankings"])


class TestRequestsThatAreRefused:
    def test_no_candidates(self, client):
        assert client.post(PATH, json=body(gpuCandidates=[])).status_code == 422

    def test_a_duplicate_worker_id(self, client):
        response = client.post(PATH, json=body(gpuCandidates=[MIDDLE, MIDDLE]))

        assert response.status_code == 422
        assert "more than once" in str(response.json()["detail"])

    def test_a_duplicate_among_several(self, client):
        duplicated = dict(FAST, workerId="worker-4070")

        assert client.post(
            PATH, json=body(gpuCandidates=[MIDDLE, SLOW, duplicated])
        ).status_code == 422

    def test_an_unknown_gpu_model(self, client):
        unknown = dict(MIDDLE, gpuModel="A GPU From The Future")

        response = client.post(PATH, json=body(gpuCandidates=[unknown]))

        assert response.status_code == 422
        assert response.json()["detail"][0]["type"] == "unknown_category"

    def test_an_unknown_base_model(self, client):
        assert client.post(
            PATH, json=body(modelId="a-model-nobody-has-seen")).status_code == 422

    def test_a_zero_benchmark(self, client):
        broken = dict(MIDDLE, benchmarkTokensPerSec=0.0)

        assert client.post(PATH, json=body(gpuCandidates=[broken])).status_code == 422

    def test_a_dataset_whose_average_exceeds_its_maximum(self, client):
        assert client.post(
            PATH, json=body(dataset=dict(DATASET, avgSeqLength=5000.0))
        ).status_code == 422

    def test_zero_epochs(self, client):
        assert client.post(
            PATH, json=body(trainingConfig=dict(TRAINING, epochs=0))
        ).status_code == 422

    def test_an_infinite_benchmark(self, client):
        import json

        raw = json.dumps(body(gpuCandidates=[dict(MIDDLE, benchmarkTokensPerSec=1.0)]))
        raw = raw.replace('"benchmarkTokensPerSec": 1.0',
                          '"benchmarkTokensPerSec": Infinity')

        response = client.post(
            PATH, content=raw, headers={"Content-Type": "application/json"})

        assert response.status_code == 422


class TestWhenTheModelIsNotThere:
    def test_there_is_no_recommendation_without_a_model(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GSHARED_SCHEDULER_MODELS_DIR", str(tmp_path))

        with TestClient(app) as client:
            response = client.post(PATH, json=body())

        assert response.status_code == 503

    def test_it_does_not_fall_back_to_the_fastest_benchmark(self, tmp_path, monkeypatch):
        # 모델 없이 benchmark로 고르면 예측을 근거로 한 추천이 아니면서
        # 그렇게 보인다. 그런 답은 만들지 않는다.
        monkeypatch.setenv("GSHARED_SCHEDULER_MODELS_DIR", str(tmp_path))

        with TestClient(app) as client:
            response = client.post(PATH, json=body())

        assert "recommendedWorkerId" not in response.text
        assert "worker-4090" not in response.text


class TestTheRuntimePredictionApiIsUnchanged:
    """이번 작업 때문에 예측 API가 달라지면 안 된다."""

    def test_it_still_answers_predictions_only(self, client):
        answer = client.post(PREDICT_PATH, json=body()).json()

        assert set(answer) == {"schedulerModelVersion", "predictions"}

    def test_it_has_no_rank(self, client):
        answer = client.post(PREDICT_PATH, json=body()).json()

        for entry in answer["predictions"]:
            assert set(entry) == {"workerId", "predictedRuntimeSec"}

    def test_it_has_no_recommendation(self, client):
        assert "recommendedWorkerId" not in client.post(PREDICT_PATH, json=body()).text

    def test_it_does_not_sort(self, client):
        # 느린 GPU를 먼저 넣으면 먼저 나온다.
        answer = client.post(PREDICT_PATH, json=body()).json()

        seconds = [entry["predictedRuntimeSec"] for entry in answer["predictions"]]
        assert seconds == sorted(seconds, reverse=True)

    def test_it_still_accepts_a_duplicate_worker(self, client):
        # 중복 거절은 추천 쪽에만 더한 규칙이다. 예측만 하는 쪽에서는 같은 Worker를
        # 두 번 묻는 것이 쓸데없을 뿐 틀린 답을 만들지 않는다.
        response = client.post(PREDICT_PATH, json=body(gpuCandidates=[MIDDLE, MIDDLE]))

        assert response.status_code == 200
        assert len(response.json()["predictions"]) == 2


class TestBothApisAgree:
    def test_the_same_job_gives_the_same_seconds_on_both_paths(self, client):
        predicted = client.post(PREDICT_PATH, json=body()).json()["predictions"]
        ranked = client.post(PATH, json=body()).json()["rankings"]

        by_worker = {e["workerId"]: e["predictedRuntimeSec"] for e in predicted}
        for entry in ranked:
            assert entry["predictedRuntimeSec"] == by_worker[entry["workerId"]]
