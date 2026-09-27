"""API 경계: 잘못된 요청이 Scheduler 안으로 들어오지 못한다는 것을 실제 HTTP로 확인.

스키마 단위 테스트가 규칙을 고정한다면, 이 파일은 그 규칙이 정말로 요청 경로에
걸려 있는지와 Backend가 받게 될 응답 모양을 고정한다.
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

DATASET = {
    "datasetSizeBytes": 1048576,
    "sampleCount": 1000,
    "totalTokens": 256000,
    "avgSeqLength": 256.0,
    "maxSeqLength": 512,
}
TRAINING = {
    "epochs": 3,
    "batchSize": 8,
    "learningRate": 0.00002,
    "maxLength": 512,
}


def post(dataset=None, training=None):
    return client.post(
        "/scheduler/input",
        json={
            "dataset": DATASET if dataset is None else dataset,
            "training": TRAINING if training is None else training,
        },
    )


class TestAValidRequest:
    def test_it_is_accepted(self):
        assert post().status_code == 200

    def test_the_answer_repeats_what_was_understood(self):
        body = post().json()

        assert body["dataset"] == DATASET
        assert body["training"] == TRAINING

    def test_the_answer_carries_the_internal_features(self):
        body = post().json()

        assert body["features"] == {
            "datasetSizeBytes": 1048576.0,
            "sampleCount": 1000.0,
            "totalTokens": 256000.0,
            "avgSeqLength": 256.0,
            "maxSeqLength": 512.0,
        }

    def test_the_values_are_the_internal_ones_untouched(self):
        # 이름만 계약을 따른다. 숫자는 예측기가 받는 것과 같은 값이어야 하므로
        # 보기 좋으라고 정수로 되돌리지 않는다.
        features = post().json()["features"]

        assert all(isinstance(value, float) for value in features.values())

    def test_the_wire_contract_stays_camel_case(self):
        body = post().json()

        assert set(body["dataset"]) == set(DATASET)
        assert set(body["training"]) == set(TRAINING)

    def test_the_features_are_camel_case_too(self):
        # 예전에는 이 객체만 snake_case였다. 같은 응답 안에서 두 가지 이름
        # 규칙을 쓰면 읽는 쪽이 필드마다 어느 쪽인지 외워야 한다.
        assert set(post().json()["features"]) == {
            "datasetSizeBytes", "sampleCount", "totalTokens",
            "avgSeqLength", "maxSeqLength",
        }

    def test_no_internal_name_leaks_into_the_response(self):
        assert not any("_" in key for key in post().json()["features"])

    def test_the_contract_covers_every_internal_feature(self):
        # 내부에 feature가 늘었는데 계약 모델에 적지 않으면 응답이 만들어질 때
        # 드러나야 한다. 조용히 빠지는 쪽이 훨씬 나쁘다.
        from app.scheduler.features import FEATURE_NAMES

        expected = {
            "".join(part.capitalize() if index else part
                    for index, part in enumerate(name.split("_")))
            for name in FEATURE_NAMES
        }
        assert set(post().json()["features"]) == expected


class TestValidationHappensAtTheBoundary:
    def test_a_bad_dataset_never_reaches_the_scheduler(self):
        response = post(dataset={**DATASET, "sampleCount": 0})

        assert response.status_code == 422

    def test_a_bad_training_config_never_reaches_the_scheduler(self):
        response = post(training={**TRAINING, "learningRate": 0})

        assert response.status_code == 422

    def test_the_error_says_which_field_in_which_half_of_the_request(self):
        # Backend가 어디를 고쳐야 하는지 알 수 있어야 한다.
        detail = post(dataset={**DATASET, "sampleCount": -1}).json()["detail"]

        assert detail[0]["loc"] == ["body", "dataset", "sampleCount"]

    def test_the_standard_fastapi_error_shape_is_kept(self):
        # 손으로 만든 에러 응답을 끼워 넣지 않았다는 것. Backend는 다른 FastAPI
        # 엔드포인트와 같은 방식으로 읽으면 된다.
        body = post(dataset={**DATASET, "sampleCount": 0}).json()

        assert set(body) == {"detail"}
        assert {"type", "loc", "msg", "input"} <= set(body["detail"][0])

    def test_every_problem_is_reported_at_once(self):
        response = post(
            dataset={**DATASET, "sampleCount": 0, "totalTokens": -1},
            training={**TRAINING, "epochs": 0},
        )

        assert len({tuple(e["loc"]) for e in response.json()["detail"]}) == 3

    def test_a_missing_half_of_the_request_is_refused(self):
        response = client.post("/scheduler/input", json={"dataset": DATASET})

        assert response.status_code == 422

    def test_an_unknown_field_is_refused_rather_than_ignored(self):
        response = post(dataset={**DATASET, "shuffle": True})

        assert response.status_code == 422

    def test_a_body_that_is_not_json_at_all_is_refused(self):
        response = client.post("/scheduler/input", content="not json")

        assert response.status_code == 422


class TestTheRestOfTheAppIsUnchanged:
    def test_health_still_answers(self):
        assert client.get("/health").json() == {"status": "ok"}

    def test_the_endpoint_is_documented(self):
        paths = client.get("/openapi.json").json()["paths"]

        assert "/scheduler/input" in paths
