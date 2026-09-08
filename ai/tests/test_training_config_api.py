"""AUTO/CUSTOM이 실제 HTTP 경계에서 어떻게 보이는지.

단위 테스트가 규칙을 고정한다면, 이 파일은 그 규칙이 정말 요청 경로에 걸려 있는지와
Backend가 받게 될 응답 모양을 고정한다.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.training import TrainingMode

client = TestClient(app)

DATASET = {
    "datasetSizeBytes": 1048576,
    "sampleCount": 1000,
    "totalTokens": 256000,
    "avgSeqLength": 256.0,
    "maxSeqLength": 4096,
}
SMALL = {
    "datasetSizeBytes": 4096,
    "sampleCount": 10,
    "totalTokens": 800,
    "avgSeqLength": 80.0,
    "maxSeqLength": 128,
}


def auto(mode, dataset=None):
    return client.post(
        "/scheduler/training-config",
        json={"dataset": DATASET if dataset is None else dataset, "mode": mode},
    )


def submit(training, dataset=None):
    return client.post(
        "/scheduler/input",
        json={"dataset": DATASET if dataset is None else dataset, "training": training},
    )


class TestTheAutoEndpoint:
    @pytest.mark.parametrize("mode", [m.value for m in TrainingMode])
    def test_every_mode_is_accepted(self, mode):
        assert auto(mode).status_code == 200

    def test_it_answers_with_the_four_values_and_nothing_else(self):
        assert set(auto("BALANCED").json()) == {
            "epochs", "batchSize", "learningRate", "maxLength"
        }

    def test_the_answer_is_camel_case_like_every_other_contract(self):
        assert "batchSize" in auto("SPEED").json()

    def test_the_three_modes_give_three_different_answers(self):
        answers = [auto(mode.value).json() for mode in TrainingMode]

        assert len({tuple(sorted(a.items())) for a in answers}) == 3

    def test_speed_is_the_shortest_run(self):
        speed, training = auto("SPEED").json(), auto("TRAINING").json()

        assert speed["epochs"] < training["epochs"]
        assert speed["maxLength"] < training["maxLength"]

    def test_a_small_dataset_shrinks_the_batch_and_the_length(self):
        answer = auto("SPEED", SMALL).json()

        assert answer["batchSize"] == 10
        assert answer["maxLength"] == 128

    def test_a_mode_that_does_not_exist_is_refused(self):
        response = auto("FAST")

        assert response.status_code == 422
        assert "SPEED" in response.json()["detail"][0]["msg"]

    def test_the_mode_is_case_sensitive(self):
        assert auto("speed").status_code == 422

    def test_a_request_without_a_mode_is_refused(self):
        response = client.post("/scheduler/training-config", json={"dataset": DATASET})

        assert response.status_code == 422

    def test_a_bad_dataset_is_refused_before_any_rule_runs(self):
        response = auto("SPEED", {**DATASET, "sampleCount": 0})

        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"] == ["body", "dataset", "sampleCount"]


class TestAutoOutputGoesStraightBackIn:
    @pytest.mark.parametrize("mode", [m.value for m in TrainingMode])
    def test_what_auto_produced_is_accepted_as_input(self, mode):
        # 두 엔드포인트가 같은 TrainingConfig 표현을 공유한다는 것의 확인.
        produced = auto(mode).json()

        assert submit(produced).status_code == 200

    @pytest.mark.parametrize("mode", [m.value for m in TrainingMode])
    def test_it_comes_back_unchanged(self, mode):
        produced = auto(mode).json()

        assert submit(produced).json()["training"] == produced

    def test_this_holds_for_a_small_dataset_too(self):
        produced = auto("SPEED", SMALL).json()

        assert submit(produced, SMALL).status_code == 200


class TestNothingIsRefusedWithoutEvidenceAtTheBoundary:
    """Schema를 통과한 설정은 이 엔드포인트에서 더 막히지 않는다.

    두 조건이 한때 거절 사유였다가 빠졌다. 승인 Docker Image의 preprocessing /
    DataLoader 계약이 정해지지 않아 실행이 실패한다고 단정할 수 없기 때문이다.
    """

    def test_a_config_that_fits_the_dataset_is_accepted(self):
        response = submit({
            "epochs": 3, "batchSize": 8, "learningRate": 0.0002, "maxLength": 512
        })

        assert response.status_code == 200

    def test_a_max_length_longer_than_the_data_is_accepted(self):
        # maxLength가 truncation 상한이고 dynamic padding을 쓴다면 문제없이 돌아간다.
        response = submit(
            {"epochs": 3, "batchSize": 8, "learningRate": 0.0002, "maxLength": 2048},
            SMALL,
        )

        assert response.status_code == 200

    def test_a_batch_larger_than_the_dataset_is_accepted(self):
        # 일반적인 DataLoader는 더 작은 배치 하나로 돌린다.
        response = submit(
            {"epochs": 3, "batchSize": 32, "learningRate": 0.0002, "maxLength": 128},
            SMALL,
        )

        assert response.status_code == 200

    def test_both_at_once_are_still_accepted(self):
        response = submit(
            {"epochs": 3, "batchSize": 32, "learningRate": 0.0002, "maxLength": 2048},
            SMALL,
        )

        assert response.status_code == 200

    def test_such_a_request_still_produces_features(self):
        # 거절되지 않았으므로 전처리까지 그대로 간다.
        body = submit(
            {"epochs": 3, "batchSize": 32, "learningRate": 0.0002, "maxLength": 2048},
            SMALL,
        ).json()

        assert body["features"]["sample_count"] == 10.0

    def test_the_schema_still_refuses_what_it_always_refused(self):
        # 정책이 비었다고 해서 아무거나 통과하는 것은 아니다.
        response = submit(
            {"epochs": 0, "batchSize": 8, "learningRate": 0.0002, "maxLength": 512}
        )

        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"] == ["body", "training", "epochs"]


class TestNothingIsSilentlyCorrected:
    def test_an_accepted_config_comes_back_exactly_as_sent(self):
        sent = {"epochs": 7, "batchSize": 3, "learningRate": 0.00001, "maxLength": 64}

        assert submit(sent).json()["training"] == sent

    def test_a_config_the_rules_would_have_flagged_comes_back_untouched(self):
        # 예전에는 거절했던 값. 이제는 통과하되, 통과시키면서 고치지도 않는다.
        sent = {"epochs": 3, "batchSize": 32, "learningRate": 0.0002, "maxLength": 2048}

        assert submit(sent, SMALL).json()["training"] == sent


class TestTheRestOfTheAppIsUnchanged:
    def test_health_still_answers(self):
        assert client.get("/health").json() == {"status": "ok"}

    def test_both_endpoints_are_documented(self):
        paths = client.get("/openapi.json").json()["paths"]

        assert {"/scheduler/input", "/scheduler/training-config"} <= set(paths)
