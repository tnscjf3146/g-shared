"""학습 파이프라인. 데이터가 들어가 모델과 metadata가 나온다.

가장 중요한 확인은 leakage다. 정답이 feature로 들어가면 검증 점수는 완벽해지고
실제 예측은 쓸모없어진다 -- 그리고 그 사실은 지표만 봐서는 알 수 없다.
"""

import json
import math

import pytest

from app.scheduler.runtime_features import (
    FEATURE_SCHEMA_VERSION,
    RUNTIME_FEATURE_NAMES,
    TARGET_NAME,
    CategoryMapping,
)
from app.training.runtime_dataset import TRAINING_DATA_TYPE, build_rows
from app.training.runtime_trainer import (
    METADATA_FILE_NAME,
    MODEL_FILE_NAME,
    MODEL_VERSION,
    TARGET_TRANSFORM,
    TrainingDataError,
    build_frame,
    train_runtime_model,
)


class TestBuildingTheFeatureFrame:
    def test_only_the_declared_columns_are_built(self, bootstrap_rows):
        frame = build_frame(bootstrap_rows)

        assert tuple(frame.columns) == RUNTIME_FEATURE_NAMES

    def test_the_target_never_becomes_a_column(self, bootstrap_rows):
        # 행에는 정답이 들어 있다. 열은 그것을 보고 정하지 않는다.
        assert TARGET_NAME in bootstrap_rows[0]

        assert TARGET_NAME not in build_frame(bootstrap_rows).columns

    def test_extra_columns_in_the_data_are_ignored(self, bootstrap_rows):
        noisy = [dict(row, worker_id="worker-1", job_id="job-9")
                 for row in bootstrap_rows[:10]]

        frame = build_frame(noisy)

        assert "worker_id" not in frame.columns
        assert "job_id" not in frame.columns

    def test_an_empty_dataset_is_refused(self):
        with pytest.raises(TrainingDataError):
            build_frame([])

    def test_a_missing_feature_is_refused(self, bootstrap_rows):
        without = [{k: v for k, v in row.items() if k != "epochs"}
                   for row in bootstrap_rows[:5]]

        with pytest.raises(TrainingDataError, match="epochs"):
            build_frame(without)


class TestTheTarget:
    def test_a_row_without_the_target_is_refused(self, bootstrap_rows):
        without = [{k: v for k, v in row.items() if k != TARGET_NAME}
                   for row in bootstrap_rows[:5]]

        with pytest.raises(TrainingDataError, match=TARGET_NAME):
            train_runtime_model(without, models_directory=None)

    @pytest.mark.parametrize("bad", [0, 0.0, -1.0])
    def test_a_runtime_of_zero_or_less_is_refused(self, bootstrap_rows, bad, tmp_path):
        rows = [dict(row) for row in bootstrap_rows[:60]]
        rows[3][TARGET_NAME] = bad

        with pytest.raises(TrainingDataError, match="greater than zero"):
            train_runtime_model(rows, models_directory=tmp_path)

    @pytest.mark.parametrize("bad", [math.nan, math.inf])
    def test_a_runtime_that_is_not_finite_is_refused(self, bootstrap_rows, bad, tmp_path):
        rows = [dict(row) for row in bootstrap_rows[:60]]
        rows[3][TARGET_NAME] = bad

        with pytest.raises(TrainingDataError, match="finite"):
            train_runtime_model(rows, models_directory=tmp_path)

    def test_a_non_numeric_runtime_is_refused(self, bootstrap_rows, tmp_path):
        rows = [dict(row) for row in bootstrap_rows[:60]]
        rows[3][TARGET_NAME] = "1842.5"

        with pytest.raises(TrainingDataError, match="must be a number"):
            train_runtime_model(rows, models_directory=tmp_path)


class TestWhatTrainingProduces:
    def test_the_model_file_is_written(self, trained_models_directory):
        assert (trained_models_directory / MODEL_FILE_NAME).is_file()

    def test_the_metadata_is_written_beside_it(self, trained_models_directory):
        assert (trained_models_directory / METADATA_FILE_NAME).is_file()

    def test_the_model_is_not_a_pickle(self, trained_models_directory):
        # XGBoost 자체 포맷(JSON). pickle은 라이브러리 버전이 바뀌면 못 읽고,
        # 신뢰할 수 없는 파일을 여는 위험도 따라온다.
        text = (trained_models_directory / MODEL_FILE_NAME).read_text(encoding="utf-8")

        assert json.loads(text)["learner"] is not None

    def test_the_metadata_records_what_it_was_trained_on(self, trained_models_directory):
        metadata = json.loads(
            (trained_models_directory / METADATA_FILE_NAME).read_text(encoding="utf-8"))

        assert metadata["modelVersion"] == MODEL_VERSION
        assert metadata["featureSchemaVersion"] == FEATURE_SCHEMA_VERSION
        assert metadata["featureSchemaVersion"] == "runtime-features-v2"
        assert metadata["featureNames"] == list(RUNTIME_FEATURE_NAMES)
        assert metadata["target"] == TARGET_NAME
        assert metadata["targetTransform"] == TARGET_TRANSFORM
        assert metadata["trainingDataType"] == TRAINING_DATA_TYPE
        assert metadata["categoryMappings"]["gpu_model"]

    def test_the_metadata_says_the_metrics_are_not_production_accuracy(
        self, trained_models_directory
    ):
        # synthetic 데이터로 잰 오차를 실제 정확도로 읽으면 안 된다.
        metadata = json.loads(
            (trained_models_directory / METADATA_FILE_NAME).read_text(encoding="utf-8"))

        assert "synthetic" in metadata["metrics"]["note"]

    def test_the_hyperparameters_are_recorded(self, trained_models_directory):
        metadata = json.loads(
            (trained_models_directory / METADATA_FILE_NAME).read_text(encoding="utf-8"))

        assert metadata["xgbParams"]["random_state"] is not None


class TestEvaluation:
    def test_it_reports_an_error_in_seconds(self, bootstrap_rows, tmp_path):
        report = train_runtime_model(bootstrap_rows, models_directory=tmp_path)

        assert report.mae_sec > 0
        assert report.mape_percent > 0

    def test_train_and_validation_are_separated(self, bootstrap_rows, tmp_path):
        report = train_runtime_model(bootstrap_rows, models_directory=tmp_path)

        assert report.validation_rows > 0
        assert report.train_rows + report.validation_rows == report.row_count

    def test_too_few_rows_skips_validation_rather_than_faking_it(self, tmp_path):
        # 20행에서 4행을 떼어 낸 점수는 숫자일 뿐 아무것도 말해 주지 않는다.
        report = train_runtime_model(build_rows(20), models_directory=tmp_path)

        assert report.validation_rows == 0
        assert report.mae_sec is None
        assert "skipped" in report.summary()


class TestReproducibility:
    def test_the_same_seed_gives_the_same_dataset(self):
        assert build_rows(50, seed=7) == build_rows(50, seed=7)

    def test_a_different_seed_gives_different_data(self):
        assert build_rows(50, seed=7) != build_rows(50, seed=8)

    def test_the_same_data_trains_to_the_same_metrics(self, bootstrap_rows, tmp_path):
        first = train_runtime_model(bootstrap_rows, models_directory=tmp_path / "a")
        second = train_runtime_model(bootstrap_rows, models_directory=tmp_path / "b")

        assert first.mae_sec == pytest.approx(second.mae_sec)

    def test_the_category_table_is_stable(self, bootstrap_rows):
        first = CategoryMapping.learned_from(bootstrap_rows).as_payload()
        second = CategoryMapping.learned_from(bootstrap_rows).as_payload()

        assert first == second


class TestTheModelActuallyLearnedSomething:
    """관계를 배우지 못한 파이프라인은 돌아가도 쓸모가 없다."""

    def test_it_beats_always_answering_the_average(self, bootstrap_rows, tmp_path):
        import numpy as np

        report = train_runtime_model(bootstrap_rows, models_directory=tmp_path)
        average = float(np.mean([row[TARGET_NAME] for row in bootstrap_rows]))
        naive_mae = float(np.mean(
            [abs(row[TARGET_NAME] - average) for row in bootstrap_rows]))

        assert report.mae_sec < naive_mae
