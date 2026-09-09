"""Feature 한 줄. 이 파일이 지키는 것은 순서와, 들어가면 안 되는 것들이다.

열 순서가 학습과 예측 사이에서 어긋나면 모델은 예외도 경고도 없이 그럴듯한
오답을 낸다. 그래서 순서를 상수 하나로 고정하고, 그 상수가 실제로 유일한
출처인지를 여기서 확인한다.
"""

import pytest

from app.schemas.runtime import GpuCandidate, RuntimeDatasetMetadata
from app.schemas.training import TrainingConfig
from app.scheduler.runtime_features import (
    CATEGORICAL_FEATURE_NAMES,
    RUNTIME_FEATURE_NAMES,
    TARGET_NAME,
    CategoryMapping,
    UnknownCategoryError,
    build_runtime_features,
)

DATASET = RuntimeDatasetMetadata(
    dataset_size_bytes=52428800, sample_count=10000, total_tokens=3500000,
    effective_tokens=2874100, avg_seq_length=350.0, max_seq_length=1800)
TRAINING = TrainingConfig(
    epochs=3, batch_size=8, learning_rate=0.0002, max_length=1024)
CANDIDATE = GpuCandidate(
    worker_id="worker-123", gpu_model="NVIDIA GeForce RTX 4070 Laptop GPU",
    vram_mb=8188, benchmark_tokens_per_sec=36521.7)


def features(candidate=CANDIDATE, model_id=3):
    return build_runtime_features(
        dataset=DATASET, training=TRAINING, model_id=model_id, candidate=candidate)


class TestWhatIsInTheRow:
    def test_the_gpu_speed_comes_from_the_measured_benchmark(self):
        assert features().benchmark_tokens_per_sec == 36521.7

    def test_the_dataset_contributes_the_tokens_that_reach_the_gpu(self):
        assert features().effective_tokens == 2874100

    def test_the_training_config_contributes_the_step_count_drivers(self):
        row = features()

        assert (row.epochs, row.batch_size, row.max_length) == (3, 8, 1024)

    def test_the_mapping_is_in_the_declared_order(self):
        assert tuple(features().as_mapping()) == RUNTIME_FEATURE_NAMES


class TestWhatIsDeliberatelyAbsent:
    """식별자와 정답은 feature가 아니다. 넣으면 모델이 관계 대신 답을 외운다."""

    def test_the_worker_id_is_not_a_feature(self):
        assert "worker_id" not in RUNTIME_FEATURE_NAMES
        assert not hasattr(features(), "worker_id")

    def test_the_target_is_not_a_feature(self):
        assert TARGET_NAME not in RUNTIME_FEATURE_NAMES

    def test_the_prediction_is_not_a_feature(self):
        assert "predicted_runtime_sec" not in RUNTIME_FEATURE_NAMES

    def test_the_old_benchmark_score_is_nowhere(self):
        assert "benchmark_score" not in RUNTIME_FEATURE_NAMES
        assert "benchmarkScore" not in RUNTIME_FEATURE_NAMES

    def test_the_measured_benchmark_is_there_instead(self):
        assert "benchmark_tokens_per_sec" in RUNTIME_FEATURE_NAMES

    def test_the_learning_rate_is_not_a_feature(self):
        # 가중치를 얼마나 움직이는지는 정하지만 step 수도 step당 계산량도 바꾸지 않는다.
        assert "learning_rate" not in RUNTIME_FEATURE_NAMES

    def test_the_docker_image_is_not_a_feature(self):
        # 승인된 이미지가 하나뿐이라 모든 행에서 같은 값이다. 값이 하나뿐인 열은
        # 정보가 없다. 계약에는 남아 있다 -- test_runtime_schema 참고.
        assert "docker_image_id" not in RUNTIME_FEATURE_NAMES


class TestTheOrderIsFixedInOnePlace:
    def test_the_vector_follows_the_declared_order(self):
        categories = CategoryMapping({
            "gpu_model": {CANDIDATE.gpu_model: 0},
            "model_id": {"3": 0},
        })

        vector = features().as_vector(categories)

        assert len(vector) == len(RUNTIME_FEATURE_NAMES)
        assert vector[RUNTIME_FEATURE_NAMES.index("epochs")] == 3.0
        assert vector[RUNTIME_FEATURE_NAMES.index("vram_mb")] == 8188.0

    def test_two_rows_of_the_same_job_differ_only_in_the_gpu(self):
        other = GpuCandidate(
            worker_id="worker-456", gpu_model="NVIDIA GeForce RTX 4070 Laptop GPU",
            vram_mb=8188, benchmark_tokens_per_sec=70000.0)

        first, second = features().as_mapping(), features(other).as_mapping()

        differing = {k for k in first if first[k] != second[k]}
        assert differing == {"benchmark_tokens_per_sec"}

    def test_every_declared_name_exists_on_the_row(self):
        row = features()

        for name in RUNTIME_FEATURE_NAMES:
            assert hasattr(row, name), name


class TestCategoryMapping:
    """문자열을 숫자로 바꾸는 표. hash()를 쓰지 않는 이유가 여기 있다."""

    def test_it_is_learned_from_the_data(self):
        rows = [
            {"gpu_model": "B", "model_id": 2},
            {"gpu_model": "A", "model_id": 1},
        ]

        mapping = CategoryMapping.learned_from(rows)

        assert mapping.known_values("gpu_model") == ("A", "B")

    def test_the_same_data_always_gives_the_same_codes(self):
        # 정렬해서 번호를 매기므로 실행마다 달라지지 않는다. 파이썬 hash()는 달라진다.
        rows = [{"gpu_model": "B", "model_id": 2}, {"gpu_model": "A", "model_id": 1}]

        first = CategoryMapping.learned_from(rows).as_payload()
        second = CategoryMapping.learned_from(list(reversed(rows))).as_payload()

        assert first == second

    def test_an_unknown_value_is_refused_rather_than_guessed(self):
        mapping = CategoryMapping({"gpu_model": {"A": 0}, "model_id": {"1": 0}})

        with pytest.raises(UnknownCategoryError) as raised:
            mapping.code_of("gpu_model", "a GPU nobody trained on")

        assert raised.value.feature == "gpu_model"
        assert "A" in str(raised.value)

    def test_the_categorical_columns_are_the_string_ones(self):
        assert set(CATEGORICAL_FEATURE_NAMES) <= set(RUNTIME_FEATURE_NAMES)
        assert "gpu_model" in CATEGORICAL_FEATURE_NAMES
