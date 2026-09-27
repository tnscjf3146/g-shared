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


#: 세 후보(8188·12288·24564 MiB)가 모두 통과하는 요구량. 걸러내기를 확인하는
#: 테스트만 이 값을 올려 잡는다.
REQUIREMENTS = {"minVramMb": 8000}


def body(**overrides):
    payload = {
        "modelId": 3, "dockerImageId": 1,
        "dataset": DATASET, "trainingConfig": TRAINING,
        "requirements": REQUIREMENTS,
        "gpuCandidates": [SLOW, MIDDLE, FAST],
    }
    payload.update(overrides)
    return payload


def predict_body(**overrides):
    """``/runtime/predict`` 용. ``requirements`` 는 추천 쪽에만 있는 필드다.

    예측은 그 GPU가 Job을 돌릴 수 있든 없든 답이 정해지는 질문이라 요구사항이
    필요 없고, ``extra="forbid"`` 라 보내면 거절된다. 그 경계도 아래에서 확인한다.
    """
    payload = body(**overrides)
    payload.pop("requirements")
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

    def test_the_top_level_has_exactly_the_four_agreed_fields(self, client):
        answer = client.post(PATH, json=body()).json()

        # requiredVramMb: 걸러내기에 실제로 쓴 VRAM 요구량 (동적 VRAM)
        assert set(answer) == {
            "schedulerModelVersion", "recommendedWorkerId", "requiredVramMb", "rankings"}

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

    def test_an_unknown_gpu_model_is_not_a_reason_to_refuse(self, client):
        # 예외: 이름만 처음 보는 GPU는 benchmark가 가장 가까운 학습 카테고리로
        # 예측된다(test_runtime_unknown_gpu.py). 모르는 기반 모델은 여전히 거절이다.
        unknown = dict(MIDDLE, gpuModel="A GPU From The Future")

        response = client.post(PATH, json=body(gpuCandidates=[unknown]))

        assert response.status_code == 200
        assert response.json()["recommendedWorkerId"] == MIDDLE["workerId"]

    @pytest.mark.parametrize("field", ["modelId", "dockerImageId"])
    @pytest.mark.parametrize("bad", [0, -1])
    def test_an_id_that_cannot_be_a_primary_key(self, client, field, bad):
        assert client.post(PATH, json=body(**{field: bad})).status_code == 422

    @pytest.mark.parametrize("field", ["modelId", "dockerImageId"])
    def test_a_numeric_string_id(self, client, field):
        # strict 정책이라 "3" 은 3으로 고쳐 주지 않는다.
        assert client.post(PATH, json=body(**{field: "3"})).status_code == 422

    def test_an_unknown_base_model(self, client):
        assert client.post(
            PATH, json=body(modelId=9999)).status_code == 422

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


class TestFeasibility:
    """돌릴 수 있는 GPU만 순위에 오른다."""

    def test_every_candidate_survives_a_low_requirement(self, client):
        answer = client.post(PATH, json=body(requirements={"minVramMb": 8000})).json()

        assert len(answer["rankings"]) == 3

    def test_a_gpu_that_is_too_small_is_dropped(self, client):
        # MIDDLE만 8188 MiB다.
        answer = client.post(PATH, json=body(requirements={"minVramMb": 12000})).json()

        workers = {entry["workerId"] for entry in answer["rankings"]}
        assert workers == {"worker-3060", "worker-4090"}

    def test_the_dropped_gpu_is_nowhere_in_the_response(self, client):
        response = client.post(PATH, json=body(requirements={"minVramMb": 12000}))

        assert "worker-4070" not in response.text

    def test_the_ranks_are_renumbered_over_what_is_left(self, client):
        answer = client.post(PATH, json=body(requirements={"minVramMb": 12000})).json()

        assert [entry["rank"] for entry in answer["rankings"]] == [1, 2]

    def test_exactly_enough_vram_is_enough(self, client):
        # 12288을 요구하고 12288을 가진 GPU. >= 다.
        answer = client.post(PATH, json=body(
            requirements={"minVramMb": 12288}, gpuCandidates=[SLOW])).json()

        assert answer["recommendedWorkerId"] == "worker-3060"

    def test_one_mib_short_is_not_enough(self, client):
        response = client.post(PATH, json=body(
            requirements={"minVramMb": 12289}, gpuCandidates=[SLOW]))

        assert response.status_code == 409

    def test_an_8_gib_laptop_gpu_reporting_8188_runs_an_8192_job(self, client):
        # RTX 4070 Laptop은 8 GiB 카드인데 드라이버가 8188로 보고한다. 이 GPU에서
        # QLoRA Runtime E2E가 끝까지 돌았으므로, 8192(8 GiB) 요구에서 떨어지면 안 된다.
        answer = client.post(PATH, json=body(
            requirements={"minVramMb": 8192}, gpuCandidates=[MIDDLE])).json()

        assert answer["recommendedWorkerId"] == "worker-4070"

    def test_the_8188_gpu_still_is_not_a_12_gib_gpu(self, client):
        response = client.post(PATH, json=body(
            requirements={"minVramMb": 12288}, gpuCandidates=[MIDDLE]))

        assert response.status_code == 409

    def test_the_recommendation_still_comes_from_the_prediction(self, client):
        # 걸러내기는 실행 가능 여부만 정한다. 남은 것 중 1위는 예측이 정한다.
        answer = client.post(PATH, json=body(requirements={"minVramMb": 12000})).json()

        assert answer["recommendedWorkerId"] == "worker-4090"
        assert answer["rankings"][0]["predictedRuntimeSec"] < (
            answer["rankings"][1]["predictedRuntimeSec"])


class TestWhenNoGpuCanRunTheJob:
    """요청도 모델도 멀쩡한데 답이 없는 경우. 셋을 섞지 않는다."""

    def test_it_is_a_conflict_not_a_validation_error(self, client):
        response = client.post(PATH, json=body(requirements={"minVramMb": 40960}))

        assert response.status_code == 409

    def test_it_says_which_kind_of_failure_it_is(self, client):
        response = client.post(PATH, json=body(requirements={"minVramMb": 40960}))

        assert response.json()["detail"][0]["type"] == "no_feasible_gpu"

    def test_the_message_names_the_requirement(self, client):
        response = client.post(PATH, json=body(requirements={"minVramMb": 40960}))

        assert "40960" in response.json()["detail"][0]["msg"]

    def test_it_points_at_the_candidates(self, client):
        response = client.post(PATH, json=body(requirements={"minVramMb": 40960}))

        assert response.json()["detail"][0]["loc"] == ["body", "gpuCandidates"]

    def test_no_worker_is_recommended_anyway(self, client):
        response = client.post(PATH, json=body(requirements={"minVramMb": 40960}))

        assert "recommendedWorkerId" not in response.text
        assert "worker-4090" not in response.text

    def test_it_is_not_the_same_as_a_missing_model(self, tmp_path, monkeypatch):
        # 503은 "나중에 다시 보내면 된다", 409는 "다시 보내도 같다"이다.
        monkeypatch.setenv("GSHARED_SCHEDULER_MODELS_DIR", str(tmp_path))

        with TestClient(app) as client:
            assert client.post(PATH, json=body()).status_code == 503

    def test_a_feasible_job_still_gets_503_when_the_model_is_gone(
            self, tmp_path, monkeypatch):
        monkeypatch.setenv("GSHARED_SCHEDULER_MODELS_DIR", str(tmp_path))

        with TestClient(app) as client:
            response = client.post(PATH, json=body(requirements={"minVramMb": 8000}))

        assert response.status_code == 503


class TestTheRequirementsField:
    def test_it_is_required(self, client):
        payload = body()
        payload.pop("requirements")

        assert client.post(PATH, json=payload).status_code == 422

    @pytest.mark.parametrize("bad", [0, -1, -12288])
    def test_a_requirement_of_zero_or_less(self, client, bad):
        assert client.post(
            PATH, json=body(requirements={"minVramMb": bad})).status_code == 422

    def test_a_numeric_string(self, client):
        assert client.post(
            PATH, json=body(requirements={"minVramMb": "12000"})).status_code == 422

    def test_a_float(self, client):
        assert client.post(
            PATH, json=body(requirements={"minVramMb": 12000.5})).status_code == 422

    def test_an_empty_requirements_object(self, client):
        assert client.post(PATH, json=body(requirements={})).status_code == 422

    def test_an_unknown_field_inside_requirements(self, client):
        assert client.post(PATH, json=body(
            requirements={"minVramMb": 8000, "minCudaVersion": "12.6"}
        )).status_code == 422

    def test_a_positive_integer_is_accepted(self, client):
        assert client.post(
            PATH, json=body(requirements={"minVramMb": 8000})).status_code == 200


class TestTheOpenApiSaysSo:
    """Backend 개발자가 코드를 읽지 않고도 계약을 알 수 있어야 한다."""

    def test_requirements_is_part_of_the_recommend_request(self, client):
        schema = client.get("/openapi.json").json()["components"]["schemas"]

        assert "requirements" in schema["RecommendationRequest"]["properties"]
        assert "requirements" in schema["RecommendationRequest"]["required"]

    def test_min_vram_mb_is_a_positive_integer(self, client):
        schema = client.get("/openapi.json").json()["components"]["schemas"]
        field = schema["JobRequirements"]["properties"]["minVramMb"]

        assert field["type"] == "integer"
        assert field["exclusiveMinimum"] == 0

    def test_the_unit_is_written_down(self, client):
        # vramMb와 같은 단위라는 것이 설명에 있어야 비교가 성립한다.
        schema = client.get("/openapi.json").json()["components"]["schemas"]

        assert "MiB" in schema["JobRequirements"]["properties"]["minVramMb"]["description"]

    def test_the_prediction_request_does_not_take_requirements(self, client):
        schema = client.get("/openapi.json").json()["components"]["schemas"]

        assert "requirements" not in schema["RuntimePredictionRequest"]["properties"]


class TestTheRuntimePredictionApiIsUnchanged:
    """이번 작업 때문에 예측 API가 달라지면 안 된다."""

    def test_it_still_answers_predictions_only(self, client):
        answer = client.post(PREDICT_PATH, json=predict_body()).json()

        assert set(answer) == {"schedulerModelVersion", "predictions"}

    def test_it_has_no_rank(self, client):
        answer = client.post(PREDICT_PATH, json=predict_body()).json()

        for entry in answer["predictions"]:
            assert set(entry) == {"workerId", "predictedRuntimeSec"}

    def test_it_has_no_recommendation(self, client):
        assert "recommendedWorkerId" not in client.post(
            PREDICT_PATH, json=predict_body()).text

    def test_it_does_not_sort(self, client):
        # 느린 GPU를 먼저 넣으면 먼저 나온다.
        answer = client.post(PREDICT_PATH, json=predict_body()).json()

        seconds = [entry["predictedRuntimeSec"] for entry in answer["predictions"]]
        assert seconds == sorted(seconds, reverse=True)

    def test_it_still_accepts_a_duplicate_worker(self, client):
        # 중복 거절은 추천 쪽에만 더한 규칙이다. 예측만 하는 쪽에서는 같은 Worker를
        # 두 번 묻는 것이 쓸데없을 뿐 틀린 답을 만들지 않는다.
        response = client.post(
            PREDICT_PATH, json=predict_body(gpuCandidates=[MIDDLE, MIDDLE]))

        assert response.status_code == 200
        assert len(response.json()["predictions"]) == 2

    def test_it_does_not_filter_by_vram(self, client):
        # 걸러내기는 추천 쪽에만 더한다. 예측은 그 GPU가 돌릴 수 있든 없든
        # "몇 초 걸리나"에 답하는 자리다.
        answer = client.post(
            PREDICT_PATH, json=predict_body(gpuCandidates=[MIDDLE])).json()

        assert len(answer["predictions"]) == 1

    def test_it_refuses_requirements(self, client):
        assert client.post(PREDICT_PATH, json=body()).status_code == 422


class TestBothApisAgree:
    def test_the_same_job_gives_the_same_seconds_on_both_paths(self, client):
        predicted = client.post(PREDICT_PATH, json=predict_body()).json()["predictions"]
        ranked = client.post(PATH, json=body()).json()["rankings"]

        by_worker = {e["workerId"]: e["predictedRuntimeSec"] for e in predicted}
        for entry in ranked:
            assert entry["predictedRuntimeSec"] == by_worker[entry["workerId"]]
