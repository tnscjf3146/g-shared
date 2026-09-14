"""Backend가 보내야 하는 요청을 파일로 고정한다.

``tests/fixtures/backend_requests/`` 의 두 JSON은 **Backend가 그대로 보내면 받아지는
요청**이다. 이 파일은 그 약속을 세 방향에서 확인한다.

* fixture 그대로 보내면 받는다.
* Backend가 실제로 저질렀던 어긋남은 받지 않는다 -- ``/training-config`` 의
  ``dataset.effectiveTokens``, ``/recommend`` 의 ``requirements`` 누락. 계약을 느슨하게
  풀어 맞추지 않는다. 보낸 쪽이 모르는 채로 틀린 값을 계속 보내게 되기 때문이다.
* fixture와 OpenAPI 스키마가 서로 어긋나지 않는다 -- 필수 필드는 fixture에 다 있고,
  fixture의 키는 스키마에 다 있다.

필드 하나하나의 범위·타입 규칙은 test_training_config_api.py·test_recommendation_api.py
가 맡는다. 여기서는 "Backend에 건넬 예시가 정말 통하는가"만 본다.
"""

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

FIXTURES = Path(__file__).parent / "fixtures" / "backend_requests"
TRAINING_CONFIG = "/scheduler/training-config"
RECOMMEND = "/scheduler/recommend"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def training_config_request():
    return load("training_config_request.json")


@pytest.fixture
def recommend_request():
    return load("recommend_request.json")


@pytest.fixture
def client(trained_models_directory, monkeypatch):
    monkeypatch.setenv("GSHARED_SCHEDULER_MODELS_DIR", str(trained_models_directory))
    with TestClient(app) as client:
        yield client


def error_types(response):
    return {(tuple(entry["loc"]), entry["type"]) for entry in response.json()["detail"]}


class TestTheTrainingConfigRequest:
    def test_the_fixture_is_accepted(self, client, training_config_request):
        response = client.post(TRAINING_CONFIG, json=training_config_request)

        assert response.status_code == 200
        assert set(response.json()) == {"epochs", "batchSize", "learningRate", "maxLength"}

    def test_effective_tokens_in_the_dataset_is_refused(self, client, training_config_request):
        # Backend가 실제로 보냈던 요청. AUTO 설정은 effectiveTokens를 쓰지 않고,
        # extra="forbid" 라 조용히 무시하지도 않는다.
        sent = copy.deepcopy(training_config_request)
        sent["dataset"]["effectiveTokens"] = sent["dataset"]["totalTokens"]

        response = client.post(TRAINING_CONFIG, json=sent)

        assert response.status_code == 422
        assert (("body", "dataset", "effectiveTokens"), "extra_forbidden") in error_types(response)

    def test_an_unknown_top_level_field_is_refused(self, client, training_config_request):
        sent = dict(training_config_request, trainingPriority="BALANCED")

        response = client.post(TRAINING_CONFIG, json=sent)

        assert response.status_code == 422
        assert (("body", "trainingPriority"), "extra_forbidden") in error_types(response)

    def test_an_unknown_dataset_field_is_refused(self, client, training_config_request):
        sent = copy.deepcopy(training_config_request)
        sent["dataset"]["storageUri"] = "datasets/a.jsonl"

        assert client.post(TRAINING_CONFIG, json=sent).status_code == 422


class TestTheRecommendRequest:
    def test_the_fixture_is_accepted(self, client, recommend_request):
        response = client.post(RECOMMEND, json=recommend_request)

        assert response.status_code == 200
        assert response.json()["recommendedWorkerId"] in {
            candidate["workerId"] for candidate in recommend_request["gpuCandidates"]}

    def test_the_fixture_carries_the_8188_laptop_gpu_against_8192(self, client, recommend_request):
        # fixture가 8 GiB 경계를 실제로 밟고 있는지 -- 다른 값으로 바뀌면 이 예시가
        # 보여 주려던 것을 더는 보여 주지 못한다.
        assert recommend_request["requirements"]["minVramMb"] == 8192
        assert 8188 in {c["vramMb"] for c in recommend_request["gpuCandidates"]}

        answer = client.post(RECOMMEND, json=recommend_request).json()
        assert "worker-4070-laptop" in {entry["workerId"] for entry in answer["rankings"]}

    def test_a_request_without_requirements_is_refused(self, client, recommend_request):
        # Backend가 실제로 보냈던 요청. minVramMb 없이는 실행 가능 여부를 판단할 수 없다.
        sent = dict(recommend_request)
        sent.pop("requirements")

        response = client.post(RECOMMEND, json=sent)

        assert response.status_code == 422
        assert (("body", "requirements"), "missing") in error_types(response)

    def test_requirements_without_min_vram_is_refused(self, client, recommend_request):
        sent = dict(recommend_request, requirements={})

        response = client.post(RECOMMEND, json=sent)

        assert response.status_code == 422
        assert (("body", "requirements", "minVramMb"), "missing") in error_types(response)

    @pytest.mark.parametrize("bad", ["8192", 8192.5, None, [8192]])
    def test_a_min_vram_of_the_wrong_type_is_refused(self, client, recommend_request, bad):
        sent = dict(recommend_request, requirements={"minVramMb": bad})

        assert client.post(RECOMMEND, json=sent).status_code == 422


class TestTheFixturesMatchTheOpenApiSchema:
    """예시가 스키마보다 앞서가거나 뒤처지면, Backend는 둘 중 틀린 쪽을 따라 만든다."""

    @pytest.fixture
    def schemas(self, client):
        return client.get("/openapi.json").json()["components"]["schemas"]

    @staticmethod
    def resolve(schemas, node):
        ref = node.get("$ref") or next(
            (option.get("$ref") for option in node.get("allOf", []) + node.get("anyOf", [])
             if option.get("$ref")), None)
        return schemas[ref.rsplit("/", 1)[-1]] if ref else node

    def assert_matches(self, schemas, schema, value, where):
        properties = schema.get("properties", {})
        assert set(schema.get("required", [])) <= set(value), (
            f"{where}: the fixture is missing required fields "
            f"{sorted(set(schema.get('required', [])) - set(value))}")
        assert set(value) <= set(properties), (
            f"{where}: the fixture has fields the schema does not declare "
            f"{sorted(set(value) - set(properties))}")
        for key, item in value.items():
            child = self.resolve(schemas, properties[key])
            if isinstance(item, dict):
                self.assert_matches(schemas, child, item, f"{where}.{key}")
            elif isinstance(item, list) and item and isinstance(item[0], dict):
                element = self.resolve(schemas, child.get("items", {}))
                for index, entry in enumerate(item):
                    self.assert_matches(schemas, element, entry, f"{where}.{key}[{index}]")

    def request_schema(self, client, schemas, path):
        body = client.get("/openapi.json").json()["paths"][path]["post"]["requestBody"]
        return self.resolve(schemas, body["content"]["application/json"]["schema"])

    def test_training_config(self, client, schemas, training_config_request):
        schema = self.request_schema(client, schemas, TRAINING_CONFIG)

        self.assert_matches(schemas, schema, training_config_request, "training-config")

    def test_recommend(self, client, schemas, recommend_request):
        schema = self.request_schema(client, schemas, RECOMMEND)

        self.assert_matches(schemas, schema, recommend_request, "recommend")

    def test_the_two_datasets_differ_exactly_by_effective_tokens(
            self, training_config_request, recommend_request):
        # 두 endpoint의 dataset은 한 필드만 다르다. Backend가 한 DTO를 두 곳에 쓰면
        # 바로 이 한 필드에서 어긋난다.
        assert set(recommend_request["dataset"]) - set(training_config_request["dataset"]) == {
            "effectiveTokens"}
        assert set(training_config_request["dataset"]) <= set(recommend_request["dataset"])
