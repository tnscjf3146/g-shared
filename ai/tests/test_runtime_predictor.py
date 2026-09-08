"""예측. 후보가 몇 개든 순서가 어긋나지 않고, 예측이 아닌 값은 나가지 않는다."""

import math
from dataclasses import replace

import pytest

from app.schemas.runtime import GpuCandidate, RuntimeDatasetMetadata
from app.schemas.training import TrainingConfig
from app.scheduler.runtime_features import UnknownCategoryError, build_runtime_features
from app.scheduler.runtime_predictor import PredictionError

DATASET = RuntimeDatasetMetadata(
    dataset_size_bytes=52428800, sample_count=10000, total_tokens=3500000,
    effective_tokens=2874100, avg_seq_length=350.0, max_seq_length=1800)
TRAINING = TrainingConfig(
    epochs=3, batch_size=8, learning_rate=0.0002, max_length=1024)

SLOW = GpuCandidate(
    worker_id="worker-slow", gpu_model="NVIDIA GeForce RTX 3060",
    vram_mb=12288, benchmark_tokens_per_sec=17000.0)
FAST = GpuCandidate(
    worker_id="worker-fast", gpu_model="NVIDIA GeForce RTX 4090",
    vram_mb=24564, benchmark_tokens_per_sec=72000.0)


def rows(*candidates, model_id="qwen2.5-7b"):
    return [
        build_runtime_features(
            dataset=DATASET, training=TRAINING, model_id=model_id, candidate=candidate)
        for candidate in candidates
    ]


class TestOneGpu:
    def test_it_answers_a_positive_number_of_seconds(self, predictor):
        seconds = predictor.predict(rows(FAST))

        assert len(seconds) == 1
        assert seconds[0] > 0
        assert math.isfinite(seconds[0])

    def test_the_same_input_gives_the_same_answer(self, predictor):
        assert predictor.predict(rows(FAST)) == predictor.predict(rows(FAST))


class TestSeveralGpus:
    def test_one_answer_per_candidate(self, predictor):
        assert len(predictor.predict(rows(SLOW, FAST))) == 2

    def test_the_order_is_the_order_they_were_given_in(self, predictor):
        forwards = predictor.predict(rows(SLOW, FAST))
        backwards = predictor.predict(rows(FAST, SLOW))

        assert forwards == tuple(reversed(backwards))

    def test_predicting_together_is_the_same_as_predicting_apart(self, predictor):
        # 한 번에 넣는 것은 빠르기 위해서지 다른 답을 얻기 위해서가 아니다.
        together = predictor.predict(rows(SLOW, FAST))
        apart = (predictor.predict(rows(SLOW))[0], predictor.predict(rows(FAST))[0])

        assert together == pytest.approx(apart)

    def test_many_candidates_at_once(self, predictor):
        candidates = [replace(row, benchmark_tokens_per_sec=20000.0 + 1000 * n)
                      for n, row in enumerate(rows(*([FAST] * 12)))]

        assert len(predictor.predict(candidates)) == 12


class TestTheDirectionOfTheAnswer:
    """맞히는 정확도가 아니라 방향이 말이 되는지. 데이터가 synthetic이므로 여기까지다."""

    def test_a_faster_gpu_is_not_predicted_to_be_slower(self, predictor):
        slow, fast = predictor.predict(rows(SLOW, FAST))

        assert fast < slow

    def test_more_epochs_take_longer(self, predictor):
        longer = build_runtime_features(
            dataset=DATASET,
            training=TrainingConfig(
                epochs=5, batch_size=8, learning_rate=0.0002, max_length=1024),
            model_id="qwen2.5-7b", candidate=FAST)

        assert predictor.predict([longer])[0] > predictor.predict(rows(FAST))[0]

    def test_more_tokens_take_longer(self, predictor):
        bigger = build_runtime_features(
            dataset=RuntimeDatasetMetadata(
                dataset_size_bytes=52428800, sample_count=40000,
                total_tokens=14000000, effective_tokens=11496400,
                avg_seq_length=350.0, max_seq_length=1800),
            training=TRAINING, model_id="qwen2.5-7b", candidate=FAST)

        assert predictor.predict([bigger])[0] > predictor.predict(rows(FAST))[0]


class TestWhatIsRefused:
    def test_no_candidates_at_all(self, predictor):
        with pytest.raises(PredictionError, match="no GPU candidates"):
            predictor.predict([])

    def test_a_gpu_nobody_trained_on(self, predictor):
        # 임의의 코드를 넣어 답을 만들지 않는다. 근거가 없기 때문이다.
        unknown = GpuCandidate(
            worker_id="worker-x", gpu_model="A GPU From The Future",
            vram_mb=99999, benchmark_tokens_per_sec=500000.0)

        with pytest.raises(UnknownCategoryError) as raised:
            predictor.predict(rows(unknown))

        assert raised.value.feature == "gpu_model"

    def test_a_base_model_nobody_trained_on(self, predictor):
        with pytest.raises(UnknownCategoryError) as raised:
            predictor.predict(rows(FAST, model_id="a-model-nobody-has-seen"))

        assert raised.value.feature == "model_id"


class TestAModelThatAnswersNonsense:
    """XGBoost가 무엇을 내놓든, 실행 시간이 아닌 값은 예측으로 나가지 않는다.

    모델은 log를 씌운 시간을 내놓으므로 여기 넣는 값도 log 공간이다. ``0.0`` 은
    1초라는 멀쩡한 답이고, 문제가 되는 것은 지수를 취했을 때 숫자가 아니게 되는
    값들이다 -- NaN, 넘쳐서 무한대가 되는 값, 0으로 가라앉는 값.
    """

    class _Answers:
        def __init__(self, values):
            self._values = values

        def predict(self, frame):
            import numpy as np

            return np.asarray(self._values, dtype="float64")

    @pytest.mark.parametrize("nonsense", [
        pytest.param([float("nan")], id="nan"),
        pytest.param([float("inf")], id="infinity"),
        pytest.param([1e9], id="overflows-to-infinity"),
        pytest.param([-1e9], id="underflows-to-zero"),
    ])
    def test_it_is_refused_rather_than_returned(self, predictor, nonsense):
        import dataclasses

        broken = dataclasses.replace(predictor, model=self._Answers(nonsense))

        with pytest.raises(PredictionError, match="not a runtime"):
            broken.predict(rows(FAST))

    def test_one_bad_answer_among_good_ones_still_fails(self, predictor):
        import dataclasses

        broken = dataclasses.replace(
            predictor, model=self._Answers([1000.0, float("nan")]))

        with pytest.raises(PredictionError):
            broken.predict(rows(SLOW, FAST))
