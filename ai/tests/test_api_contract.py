"""계약을 얼려 둔다.

다른 테스트 파일들은 각 기능이 **맞게 동작하는지**를 본다. 이 파일은 그 기능들이
Backend에게 **무엇을 약속했는지**를 본다 -- 엔드포인트 목록, 같은 뜻을 가진 값의
타입, 응답의 필드 구성, 실패를 가르는 상태 코드.

여기 있는 테스트가 깨졌다면 대개 버그가 아니라 **계약이 바뀐 것**이다. 그럴 때는
테스트를 고치기 전에 docs/scheduler-api-contract.md 와 Backend 쪽에 알릴 일이
있는지부터 확인해야 한다.

기존 파일이 이미 보장하는 것은 여기서 다시 쓰지 않는다. 예를 들어 순위의 재현성은
test_gpu_ranking.py, 걸러내기 규칙은 test_feasibility.py, 구버전 artifact의 기동
실패는 test_runtime_model_load.py 가 각각 맡는다.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.scheduler.runtime_features import RUNTIME_FEATURE_NAMES, FEATURE_SCHEMA_VERSION
from app.training.runtime_trainer import MODEL_VERSION

DATASET = {
    "datasetSizeBytes": 52428800, "sampleCount": 10000, "totalTokens": 3500000,
    "effectiveTokens": 2874100, "avgSeqLength": 350.0, "maxSeqLength": 1800,
}
TRAINING = {"epochs": 3, "batchSize": 8, "learningRate": 0.0002, "maxLength": 1024}
SMALL = {"workerId": "worker-4070", "gpuModel": "NVIDIA GeForce RTX 4070 Laptop GPU",
         "vramMb": 8188, "benchmarkTokensPerSec": 36521.7}
LARGE = {"workerId": "worker-4090", "gpuModel": "NVIDIA GeForce RTX 4090",
         "vramMb": 24564, "benchmarkTokensPerSec": 72000.0}

RECOMMEND = "/scheduler/recommend"
PREDICT = "/scheduler/runtime/predict"


def recommend_body(**overrides):
    payload = {
        "modelId": 3, "dockerImageId": 1,
        "dataset": DATASET, "trainingConfig": TRAINING,
        "requirements": {"minVramMb": 8000},
        "gpuCandidates": [SMALL, LARGE],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def client(trained_models_directory, monkeypatch):
    monkeypatch.setenv("GSHARED_SCHEDULER_MODELS_DIR", str(trained_models_directory))
    with TestClient(app) as client:
        yield client


@pytest.fixture
def schemas(client):
    return client.get("/openapi.json").json()["components"]["schemas"]


class TestTheEndpointSurface:
    """공개된 입구는 이 다섯 개다. 늘어나는 것도 계약 변경이다."""

    def test_these_are_all_of_them(self, client):
        paths = client.get("/openapi.json").json()["paths"]

        assert set(paths) == {
            "/health",
            "/scheduler/training-config",
            "/scheduler/input",
            "/scheduler/runtime/predict",
            "/scheduler/recommend",
        }

    def test_every_scheduler_endpoint_is_a_post(self, client):
        paths = client.get("/openapi.json").json()["paths"]

        for path, operations in paths.items():
            if path.startswith("/scheduler/"):
                assert set(operations) == {"post"}, path

    def test_health_is_a_get(self, client):
        assert set(client.get("/openapi.json").json()["paths"]["/health"]) == {"get"}


class TestTheSameThingHasTheSameTypeEverywhere:
    """한 값이 두 스키마에 나오면 두 곳의 타입이 같아야 한다.

    ``/runtime/predict`` 와 ``/recommend`` 는 같은 작업 입력을 받는다. 한쪽만
    바뀌는 날 Backend는 같은 값을 두 가지로 만들어야 한다.
    """

    @pytest.mark.parametrize("field", ["modelId", "dockerImageId"])
    def test_the_two_request_schemas_agree(self, schemas, field):
        predict = schemas["RuntimePredictionRequest"]["properties"][field]
        recommend = schemas["RecommendationRequest"]["properties"][field]

        assert predict == recommend

    @pytest.mark.parametrize("field", ["modelId", "dockerImageId"])
    def test_the_catalog_ids_are_positive_integers(self, schemas, field):
        # Backend의 base_models / workloads PK 그대로다. 문자열이 아니다.
        spec = schemas["RecommendationRequest"]["properties"][field]

        assert spec["type"] == "integer"
        assert spec["exclusiveMinimum"] == 0

    def test_the_two_vram_fields_are_the_same_kind_of_number(self, schemas):
        # 이 둘을 >= 로 비교하므로 타입이 갈리면 비교가 뜻을 잃는다.
        required = schemas["JobRequirements"]["properties"]["minVramMb"]
        supplied = schemas["GpuCandidate"]["properties"]["vramMb"]

        assert required["type"] == supplied["type"] == "integer"
        assert required["exclusiveMinimum"] == supplied["exclusiveMinimum"] == 0

    def test_both_vram_fields_say_their_unit(self, schemas):
        # 단위가 안 적힌 숫자 두 개를 비교하게 두지 않는다.
        assert "MiB" in schemas["JobRequirements"]["properties"]["minVramMb"][
            "description"]

    def test_the_predicted_seconds_are_the_same_shape_in_both_responses(self, schemas):
        # 설명 문구는 각자 맥락이 있어 다를 수 있다. 계약인 것은 타입과 제약이다.
        predict = schemas["RuntimePrediction"]["properties"]["predictedRuntimeSec"]
        rank = schemas["RankedPrediction"]["properties"]["predictedRuntimeSec"]

        assert predict["type"] == rank["type"] == "number"
        assert predict["exclusiveMinimum"] == rank["exclusiveMinimum"] == 0


class TestUnknownFieldsAreRefusedEverywhere:
    """조용히 무시하면 Backend는 오타 난 필드를 계속 보내면서 반영됐다고 믿는다."""

    @pytest.mark.parametrize("name", [
        "RecommendationRequest", "RuntimePredictionRequest", "JobRequirements",
        "GpuCandidate", "TrainingConfig", "DatasetMetadata", "RuntimeDatasetMetadata",
        "SchedulerInputRequest", "AutoTrainingConfigRequest",
    ])
    def test_the_schema_forbids_extra_fields(self, schemas, name):
        assert schemas[name]["additionalProperties"] is False


class TestTheTwoVersionsAreDifferentThings:
    """``runtime-features-v2`` 와 ``runtime-xgb-v1`` 은 합쳐질 값이 아니다.

    앞의 것은 "어떤 열로 학습됐는가"라서 열 구성이 바뀔 때 오르고, 뒤의 것은
    "어느 모델이 답했는가"라서 같은 열로 다시 학습해도 오른다. 하나로 묶으면
    재학습마다 멀쩡한 artifact가 호환되지 않는다고 거절되거나, 열이 바뀌었는데
    같은 버전으로 통과한다.
    """

    def test_they_are_not_the_same_string(self):
        assert FEATURE_SCHEMA_VERSION != MODEL_VERSION

    def test_the_response_carries_the_model_version(self, client):
        answer = client.post(RECOMMEND, json=recommend_body()).json()

        assert answer["schedulerModelVersion"] == MODEL_VERSION

    def test_the_response_does_not_leak_the_feature_schema_version(self, client):
        # 내부 호환성 값이다. Backend가 쓸 일이 없고, 쓰기 시작하면 못 바꾼다.
        response = client.post(RECOMMEND, json=recommend_body())

        assert FEATURE_SCHEMA_VERSION not in response.text


class TestTheRuntimeFeatureSetIsFrozen:
    """열 열한 개. 순서까지 계약이다 -- 어긋나면 모델이 조용히 오답을 낸다."""

    def test_the_names_and_their_order(self):
        assert RUNTIME_FEATURE_NAMES == (
            "benchmark_tokens_per_sec", "vram_mb",
            "sample_count", "effective_tokens", "avg_seq_length", "max_seq_length",
            "epochs", "batch_size", "max_length",
            "gpu_model", "model_id",
        )

    def test_there_are_eleven(self):
        assert len(RUNTIME_FEATURE_NAMES) == 11

    @pytest.mark.parametrize("absent", [
        "min_vram_mb",        # 실행 가능 여부를 가르는 제약이지 실행 시간의 원인이 아니다
        "docker_image_id",    # 승인 이미지가 하나뿐이라 모든 행에서 같은 값
        "dataset_size_bytes", # 디스크 크기이지 GPU 계산량이 아니다
        "total_tokens",       # effective_tokens와 거의 같은 정보다
        "learning_rate",      # step 수도 step당 계산량도 바꾸지 않는다
        "worker_id",          # 식별자다. 학습하면 관계 대신 이름을 외운다
    ])
    def test_what_is_deliberately_not_a_feature(self, absent):
        assert absent not in RUNTIME_FEATURE_NAMES


class TestTheResponseFieldSets:
    """응답에 들어 있는 것이 전부다. 하나 늘면 Backend가 그것을 쓰기 시작한다."""

    def test_recommend_answers_exactly_three_fields(self, client):
        answer = client.post(RECOMMEND, json=recommend_body()).json()

        assert set(answer) == {
            "schedulerModelVersion", "recommendedWorkerId", "rankings"}

    def test_each_ranking_entry_answers_exactly_three_fields(self, client):
        answer = client.post(RECOMMEND, json=recommend_body()).json()

        for entry in answer["rankings"]:
            assert set(entry) == {"rank", "workerId", "predictedRuntimeSec"}

    @pytest.mark.parametrize("absent", [
        "modelId", "dockerImageId", "requirements", "dataset", "trainingConfig",
        "rejectedCandidates", "feasible", "estimatedCost", "price",
    ])
    def test_what_the_recommendation_does_not_answer(self, client, absent):
        # 되돌려 보내기(echo)도, 가격도, 탈락 목록도 없다. 비용은 Backend가 정한다.
        answer = client.post(RECOMMEND, json=recommend_body()).json()

        assert absent not in answer

    def test_the_ranks_start_at_one_and_are_continuous(self, client):
        answer = client.post(RECOMMEND, json=recommend_body()).json()

        assert [entry["rank"] for entry in answer["rankings"]] == [1, 2]


class TestTheThreeWaysToGetNoAnswer:
    """422 / 409 / 503. Backend가 셋에 대해 해야 할 일이 다르다."""

    def test_a_malformed_request_is_422(self, client):
        assert client.post(
            RECOMMEND, json=recommend_body(modelId="3")).status_code == 422

    def test_no_gpu_big_enough_is_409(self, client):
        assert client.post(RECOMMEND, json=recommend_body(
            requirements={"minVramMb": 40960})).status_code == 409

    def test_no_runtime_model_is_503(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GSHARED_SCHEDULER_MODELS_DIR", str(tmp_path))

        with TestClient(app) as client:
            assert client.post(RECOMMEND, json=recommend_body()).status_code == 503

    def test_the_three_are_not_the_same_code(self, client, tmp_path, monkeypatch):
        # 한 줄로 못 박는다. 셋이 같은 코드로 뭉개지면 Backend는 재시도해야 할
        # 때와 요청을 고쳐야 할 때를 구분할 수 없다.
        malformed = client.post(RECOMMEND, json=recommend_body(modelId="3"))
        infeasible = client.post(
            RECOMMEND, json=recommend_body(requirements={"minVramMb": 40960}))

        monkeypatch.setenv("GSHARED_SCHEDULER_MODELS_DIR", str(tmp_path))
        with TestClient(app) as bare:
            unavailable = bare.post(RECOMMEND, json=recommend_body())

        assert len({malformed.status_code, infeasible.status_code,
                    unavailable.status_code}) == 3

    def test_the_domain_error_names_itself(self, client):
        # 상태 코드만으로는 왜인지 모른다. type이 그 답이다.
        answer = client.post(RECOMMEND, json=recommend_body(
            requirements={"minVramMb": 40960})).json()

        assert answer["detail"][0]["type"] == "no_feasible_gpu"


class TestHealthDoesNotMeanReadyToPredict:
    """``/health`` 는 프로세스가 살아 있다는 뜻이다. 그 이상이 아니다.

    Backend가 health 200을 보고 "예측을 받을 준비가 됐다"고 읽으면 안 된다.
    준비 여부는 예측을 요청해 봐야 안다.
    """

    def test_it_answers_even_with_no_runtime_model(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GSHARED_SCHEDULER_MODELS_DIR", str(tmp_path))

        with TestClient(app) as client:
            assert client.get("/health").status_code == 200

    def test_and_the_recommendation_is_503_at_that_very_moment(
            self, tmp_path, monkeypatch):
        monkeypatch.setenv("GSHARED_SCHEDULER_MODELS_DIR", str(tmp_path))

        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            assert client.post(RECOMMEND, json=recommend_body()).status_code == 503

    def test_it_says_nothing_about_the_model(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GSHARED_SCHEDULER_MODELS_DIR", str(tmp_path))

        with TestClient(app) as client:
            assert client.get("/health").json() == {"status": "ok"}


class TestTheEndpointsKeepTheirOwnJobs:
    """예측은 사실, 추천은 결정. 한쪽이 다른 쪽 일을 하기 시작하면 둘 다 흐려진다."""

    def test_predict_does_not_take_requirements(self, client):
        assert client.post(PREDICT, json=recommend_body()).status_code == 422

    def test_predict_does_not_filter_by_vram(self, client):
        # 8188 MiB 후보 하나. 추천 쪽이라면 요구량에 따라 걸러질 수 있지만
        # 예측은 "몇 초 걸리나"에만 답한다.
        payload = recommend_body(gpuCandidates=[SMALL])
        payload.pop("requirements")

        assert len(client.post(PREDICT, json=payload).json()["predictions"]) == 1

    def test_predict_does_not_rank_or_recommend(self, client):
        payload = recommend_body()
        payload.pop("requirements")

        assert set(client.post(PREDICT, json=payload).json()) == {
            "schedulerModelVersion", "predictions"}

    def test_the_config_endpoints_do_not_touch_gpus(self, schemas):
        # AUTO 설정과 입력 검증은 GPU를 모른다.
        for name in ("AutoTrainingConfigRequest", "SchedulerInputRequest"):
            assert "gpuCandidates" not in schemas[name]["properties"]
            assert "requirements" not in schemas[name]["properties"]
