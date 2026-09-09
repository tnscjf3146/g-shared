"""예측 → 순위 → 추천의 배선.

XGBoost를 띄우지 않는다. 여기서 확인할 것은 예측의 정확도가 아니라 **연결**이다 --
예측기가 내놓은 숫자가 올바른 후보에 붙어 순위가 되는지. 진짜 모델로 도는 것은
API 테스트와 실제 실행에서 확인한다.
"""

import pytest

from app.schemas.recommendation import RecommendationRequest
from app.scheduler.feasibility import NoFeasibleGpuError
from app.scheduler.ranking import RankingError
from app.scheduler.recommendation import recommend_worker
from app.scheduler.runtime_features import UnknownCategoryError

DATASET = {
    "datasetSizeBytes": 52428800, "sampleCount": 10000, "totalTokens": 3500000,
    "effectiveTokens": 2874100, "avgSeqLength": 350.0, "maxSeqLength": 1800,
}
TRAINING = {"epochs": 3, "batchSize": 8, "learningRate": 0.0002, "maxLength": 1024}


def candidate(worker_id, benchmark=36521.7, vram=8188,
              gpu_model="NVIDIA GeForce RTX 4070 Laptop GPU"):
    return {"workerId": worker_id, "gpuModel": gpu_model,
            "vramMb": vram, "benchmarkTokensPerSec": benchmark}


#: 후보 전부가 통과하는 요구량. 걸러내기를 확인하는 테스트만 이 값을 올린다.
LOW_REQUIREMENT = {"minVramMb": 8000}


def request_for(*worker_ids, **overrides):
    payload = {
        "modelId": 3, "dockerImageId": 1,
        "dataset": DATASET, "trainingConfig": TRAINING,
        "requirements": LOW_REQUIREMENT,
        "gpuCandidates": [candidate(worker_id) for worker_id in worker_ids],
    }
    payload.update(overrides)
    return RecommendationRequest(**payload)


class Predictor:
    """예측기 대역. 넘겨받은 행 순서대로 정해진 숫자를 돌려준다."""

    def __init__(self, *seconds, raises=None):
        self._seconds = seconds
        self._raises = raises
        self.rows = None

    def predict(self, rows):
        self.rows = list(rows)
        if self._raises is not None:
            raise self._raises
        return tuple(self._seconds)


class TestTheWiring:
    def test_the_fastest_candidate_is_recommended(self):
        # worker-a → 1000, worker-b → 500, worker-c → 2000
        chosen = recommend_worker(
            request_for("worker-a", "worker-b", "worker-c"),
            Predictor(1000.0, 500.0, 2000.0))

        assert chosen.recommended_worker_id == "worker-b"
        assert chosen.rankings[0].rank == 1
        assert chosen.rankings[0].worker_id == "worker-b"

    def test_each_prediction_lands_on_the_candidate_it_belongs_to(self):
        chosen = recommend_worker(
            request_for("worker-a", "worker-b", "worker-c"),
            Predictor(1000.0, 500.0, 2000.0))

        seconds = {entry.worker_id: entry.predicted_runtime_sec
                   for entry in chosen.rankings}
        assert seconds == {"worker-a": 1000.0, "worker-b": 500.0, "worker-c": 2000.0}

    def test_all_candidates_are_predicted_in_one_call(self):
        # 후보마다 따로 부르면 느리고, 순서가 어긋날 자리가 생긴다.
        predictor = Predictor(1000.0, 500.0, 2000.0)

        recommend_worker(request_for("worker-a", "worker-b", "worker-c"), predictor)

        assert len(predictor.rows) == 3

    def test_the_rows_carry_the_job_and_differ_only_in_the_gpu(self):
        predictor = Predictor(1000.0, 500.0)

        recommend_worker(request_for("worker-a", "worker-b"), predictor)

        first, second = (row.as_mapping() for row in predictor.rows)
        assert first["epochs"] == 3 and first["effective_tokens"] == 2874100
        assert first == second  # 같은 사양의 두 후보이므로 feature도 같다

    def test_a_single_candidate_still_gets_a_ranking_and_a_recommendation(self):
        chosen = recommend_worker(request_for("worker-only"), Predictor(1234.5))

        assert chosen.recommended_worker_id == "worker-only"
        assert len(chosen.rankings) == 1

    def test_the_worker_id_never_reaches_the_predictor(self):
        # 식별자는 feature가 아니다. 답을 붙일 꼬리표일 뿐이다.
        predictor = Predictor(1000.0)

        recommend_worker(request_for("worker-a"), predictor)

        assert "worker_id" not in predictor.rows[0].as_mapping()


class TestFeasibilityRunsFirst:
    """예측기에 닿기 전에 걸러진다.

    최종 응답만 보면 "예측한 뒤에 지우는" 구현도 통과한다. 그래서 여기서는
    예측기가 **무엇을 받았는지**를 본다 -- 그것이 순서를 증명하는 유일한 자리다.
    """

    def test_an_infeasible_candidate_never_reaches_the_predictor(self):
        predictor = Predictor(1000.0)

        recommend_worker(
            RecommendationRequest(
                modelId=3, dockerImageId=1, dataset=DATASET, trainingConfig=TRAINING,
                requirements={"minVramMb": 12000},
                gpuCandidates=[candidate("worker-8g", vram=8192),
                               candidate("worker-24g", vram=24576)]),
            predictor)

        assert len(predictor.rows) == 1

    def test_the_row_that_arrives_is_the_feasible_one(self):
        predictor = Predictor(1000.0)

        recommend_worker(
            RecommendationRequest(
                modelId=3, dockerImageId=1, dataset=DATASET, trainingConfig=TRAINING,
                requirements={"minVramMb": 12000},
                gpuCandidates=[candidate("worker-8g", vram=8192),
                               candidate("worker-24g", vram=24576)]),
            predictor)

        assert predictor.rows[0].as_mapping()["vram_mb"] == 24576

    def test_the_predictor_is_not_called_at_all_when_nothing_fits(self):
        # 돌지 않을 GPU의 실행 시간을 묻지 않는다. 한 번도.
        predictor = Predictor(1000.0)

        with pytest.raises(NoFeasibleGpuError):
            recommend_worker(
                RecommendationRequest(
                    modelId=3, dockerImageId=1, dataset=DATASET,
                    trainingConfig=TRAINING,
                    requirements={"minVramMb": 40960},
                    gpuCandidates=[candidate("worker-8g", vram=8192),
                                   candidate("worker-24g", vram=24576)]),
                predictor)

        assert predictor.rows is None

    def test_the_dropped_candidate_is_not_in_the_rankings(self):
        chosen = recommend_worker(
            RecommendationRequest(
                modelId=3, dockerImageId=1, dataset=DATASET, trainingConfig=TRAINING,
                requirements={"minVramMb": 12000},
                gpuCandidates=[candidate("worker-8g", vram=8192),
                               candidate("worker-12g", vram=12288),
                               candidate("worker-24g", vram=24576)]),
            Predictor(500.0, 2000.0))

        assert [entry.worker_id for entry in chosen.rankings] == [
            "worker-12g", "worker-24g"]

    def test_the_recommendation_is_never_an_infeasible_gpu(self):
        # 8GB가 제일 빠르다고 나와도 돌릴 수 없으면 추천이 아니다.
        chosen = recommend_worker(
            RecommendationRequest(
                modelId=3, dockerImageId=1, dataset=DATASET, trainingConfig=TRAINING,
                requirements={"minVramMb": 12000},
                gpuCandidates=[candidate("worker-8g", vram=8192, benchmark=99000.0),
                               candidate("worker-24g", vram=24576)]),
            Predictor(1.0))

        assert chosen.recommended_worker_id == "worker-24g"

    def test_a_boundary_candidate_is_predicted(self):
        predictor = Predictor(1000.0)

        chosen = recommend_worker(
            RecommendationRequest(
                modelId=3, dockerImageId=1, dataset=DATASET, trainingConfig=TRAINING,
                requirements={"minVramMb": 12288},
                gpuCandidates=[candidate("worker-exact", vram=12288)]),
            predictor)

        assert len(predictor.rows) == 1
        assert chosen.recommended_worker_id == "worker-exact"


class TestDeterminism:
    def test_a_tie_always_recommends_the_same_worker(self):
        forwards = recommend_worker(
            request_for("worker-b", "worker-a"), Predictor(1000.0, 1000.0))
        backwards = recommend_worker(
            request_for("worker-a", "worker-b"), Predictor(1000.0, 1000.0))

        assert forwards.recommended_worker_id == "worker-a"
        assert backwards.recommended_worker_id == "worker-a"

    def test_the_whole_ranking_is_stable_under_a_tie(self):
        forwards = recommend_worker(
            request_for("worker-b", "worker-a"), Predictor(1000.0, 1000.0))
        backwards = recommend_worker(
            request_for("worker-a", "worker-b"), Predictor(1000.0, 1000.0))

        assert forwards.rankings == backwards.rankings

    def test_running_it_twice_gives_the_same_answer(self):
        request = request_for("worker-a", "worker-b", "worker-c")

        first = recommend_worker(request, Predictor(1000.0, 500.0, 2000.0))
        second = recommend_worker(request, Predictor(1000.0, 500.0, 2000.0))

        assert first == second


class TestWhatItRefuses:
    def test_a_prediction_that_is_not_a_runtime(self):
        with pytest.raises(RankingError):
            recommend_worker(
                request_for("worker-a", "worker-b"), Predictor(1000.0, float("nan")))

    def test_a_prediction_of_zero(self):
        with pytest.raises(RankingError, match="greater than zero"):
            recommend_worker(
                request_for("worker-a", "worker-b"), Predictor(1000.0, 0.0))

    def test_a_failing_predictor_is_not_swallowed(self):
        # 모델이 답하지 못하면 추천도 없다. benchmark가 높은 GPU로 물러서지 않는다.
        with pytest.raises(UnknownCategoryError):
            recommend_worker(
                request_for("worker-a"),
                Predictor(raises=UnknownCategoryError("gpu_model", "?", ())))


class TestDuplicateCandidates:
    """같은 Worker가 두 번 오면 한 기계가 두 순위를 차지한다."""

    def test_it_is_refused_at_the_request_boundary(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="more than once"):
            request_for("worker-a", "worker-a")

    def test_the_duplicate_is_named(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="worker-a"):
            request_for("worker-b", "worker-a", "worker-a")

    def test_it_is_not_silently_de_duplicated(self):
        # 조용히 하나를 지우면 보낸 쪽은 무엇을 잘못했는지 모른다.
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            request_for("worker-a", "worker-a", "worker-b")

    def test_distinct_workers_on_the_same_gpu_model_are_fine(self):
        # 같은 모델의 서로 다른 기계는 정상적인 후보다.
        chosen = recommend_worker(
            request_for("worker-a", "worker-b"), Predictor(1000.0, 500.0))

        assert len(chosen.rankings) == 2
