"""재학습이 만드는 것은 후보이고, 현역은 그대로 있어야 한다.

두 가지가 이 파일의 전부다.

**현역 모델을 덮어쓰지 않는다.** 후보가 검증 오차는 더 낮게 나올 수 있고, 그
검증셋이 합성 데이터라면 그 숫자는 XGBoost가 생성기의 공식을 얼마나 잘 외웠는지를
말할 뿐이다. 자동 승격은 없다.

**지표를 섞지 않는다.** Bootstrap Validation과 Measured Validation은 따로 계산돼
따로 보고된다. 실측 행이 적으면 실측 지표는 계산되지 않고 ``INSUFFICIENT_DATA``
로 남는다 -- 숫자를 내면 인용되고, 인용된 뒤에는 몇 건으로 잰 것인지 아무도 다시
보지 않는다.
"""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import pytest

from app.scheduler.runtime_features import RUNTIME_FEATURE_NAMES, TARGET_NAME
from app.training.provenance import PROVENANCE_FIELD, Provenance
from app.training.retrain import (
    CANDIDATE_VERSION,
    CANDIDATES_DIR_NAME,
    INSUFFICIENT_DATA,
    MIN_MEASURED_FOR_VALIDATION,
    NOT_VALIDATED,
    PENDING,
    compose,
    measured_only,
    retrain,
    verify_candidate,
)
from app.training.runtime_trainer import (
    METADATA_FILE_NAME,
    MODEL_FILE_NAME,
    MODEL_VERSION,
)

from tests.test_runtime_history import a_record

AI = Path(__file__).parents[1]
BOOTSTRAP = AI / "data" / "runtime_bootstrap_synthetic.jsonl"


@pytest.fixture
def models(tmp_path: Path) -> Path:
    """현역 모델이 놓인 디렉터리의 사본. 실제 것은 건드리지 않는다."""
    directory = tmp_path / "models"
    directory.mkdir()
    for name in (MODEL_FILE_NAME, METADATA_FILE_NAME):
        shutil.copy(AI / "models" / name, directory / name)
    return directory


def many_records(count: int, *, gpus=("NVIDIA GeForce RTX 4070 Laptop GPU",)):
    """서로 다른 COMPLETED Job ``count`` 건."""
    records = []
    for index in range(count):
        records.append(a_record(
            job_id=str(2000 + index),
            gpu_model=gpus[index % len(gpus)],
            sample_count=240 + index * 12,
            effective_tokens=25536 + index * 1280,
            epochs=1 + index % 3,
            **{TARGET_NAME: 30.0 + index * 1.7},
        ))
    return records


# ------------------------------------------------------- 현역은 그대로
class TestTheIncumbentIsNotTouched:
    def test_the_candidate_goes_somewhere_else(self, models):
        report = retrain(history=[], bootstrap_path=BOOTSTRAP,
                         models_directory=models)

        assert report.candidate_directory == (
            models / CANDIDATES_DIR_NAME / CANDIDATE_VERSION)
        assert report.candidate_directory.is_dir()

    def test_the_incumbent_files_are_unchanged(self, models):
        before = {name: (models / name).read_bytes()
                  for name in (MODEL_FILE_NAME, METADATA_FILE_NAME)}

        report = retrain(history=[], bootstrap_path=BOOTSTRAP,
                         models_directory=models)

        for name, content in before.items():
            assert (models / name).read_bytes() == content
        assert report.incumbent_untouched

    def test_the_candidate_carries_its_own_version(self, models):
        report = retrain(history=[], bootstrap_path=BOOTSTRAP,
                         models_directory=models)
        metadata = json.loads(
            report.training.metadata_path.read_text(encoding="utf-8"))

        assert metadata["modelVersion"] == CANDIDATE_VERSION
        assert metadata["modelVersion"] != MODEL_VERSION
        assert metadata["incumbentModelVersion"] == MODEL_VERSION

    def test_nothing_is_promoted(self, models):
        report = retrain(history=[], bootstrap_path=BOOTSTRAP,
                         models_directory=models)
        metadata = json.loads(
            report.training.metadata_path.read_text(encoding="utf-8"))

        assert metadata["promoted"] is False

    def test_the_incumbent_still_loads_afterwards(self, models):
        from app.scheduler.runtime_predictor import load_runtime_predictor

        retrain(history=[], bootstrap_path=BOOTSTRAP, models_directory=models)
        predictor = load_runtime_predictor(models)

        assert predictor is not None


# ------------------------------------------------------- 후보가 실제로 읽힌다
class TestTheCandidateIsReloaded:
    def test_it_loads_through_the_real_predictor(self, models):
        report = retrain(history=many_records(3), bootstrap_path=BOOTSTRAP,
                         models_directory=models)

        answers = verify_candidate(report.candidate_directory,
                                   [a_record()])

        assert len(answers) == 1

    def test_its_answers_are_real_runtimes(self, models):
        report = retrain(history=many_records(3), bootstrap_path=BOOTSTRAP,
                         models_directory=models)

        answers = verify_candidate(report.candidate_directory,
                                   many_records(5))

        for seconds in answers:
            assert math.isfinite(seconds)
            assert seconds > 0

    def test_its_feature_schema_matches_the_incumbent(self, models):
        report = retrain(history=[], bootstrap_path=BOOTSTRAP,
                         models_directory=models)
        candidate = json.loads(
            report.training.metadata_path.read_text(encoding="utf-8"))
        incumbent = json.loads(
            (models / METADATA_FILE_NAME).read_text(encoding="utf-8"))

        assert candidate["featureNames"] == incumbent["featureNames"]
        assert candidate["featureSchemaVersion"] == incumbent[
            "featureSchemaVersion"]
        assert candidate["target"] == incumbent["target"]
        assert candidate["featureCount"] == 11


# ------------------------------------------------------- 데이터 구성
class TestComposingTheTrainingSet:
    def test_bootstrap_rows_are_kept(self, models):
        report = retrain(history=many_records(3), bootstrap_path=BOOTSTRAP,
                         models_directory=models)

        assert report.bootstrap_row_count == 1200

    def test_measured_rows_are_added(self, models):
        report = retrain(history=many_records(7), bootstrap_path=BOOTSTRAP,
                         models_directory=models)

        assert report.measured_row_count == 7

    def test_rejected_rows_are_counted_and_explained(self, models):
        history = many_records(3) + [
            a_record("9001", status="CANCELLED"),
            a_record("9002", status="FAILED"),
        ]

        report = retrain(history=history, bootstrap_path=BOOTSTRAP,
                         models_directory=models)

        assert report.measured_row_count == 3
        assert report.rejected_row_count == 2
        assert report.rejection_reasons == {"NOT_COMPLETED": 2}

    def test_both_provenances_survive_into_the_metadata(self, models):
        report = retrain(history=many_records(4), bootstrap_path=BOOTSTRAP,
                         models_directory=models)
        metadata = json.loads(
            report.training.metadata_path.read_text(encoding="utf-8"))

        assert metadata["provenanceCounts"] == {"BOOTSTRAP": 1200,
                                                "MEASURED": 4}
        assert metadata["trainingDataKind"] == "BOOTSTRAP"

    def test_a_measured_only_set_can_be_taken(self):
        rows = compose(
            [{PROVENANCE_FIELD: Provenance.BOOTSTRAP.value, "a": 1}],
            [{PROVENANCE_FIELD: Provenance.MEASURED.value, "a": 2}])

        assert len(rows) == 2
        assert measured_only(rows) == [
            {PROVENANCE_FIELD: Provenance.MEASURED.value, "a": 2}]

    def test_measured_only_keeps_production_rows_too(self):
        rows = [{PROVENANCE_FIELD: Provenance.PRODUCTION.value, "a": 1},
                {PROVENANCE_FIELD: Provenance.CALIBRATION.value, "a": 2}]

        assert measured_only(rows) == [rows[0]]

    def test_composing_does_not_mutate_its_inputs(self):
        bootstrap = [{"a": 1}]
        composed = compose(bootstrap, [{"a": 2}])
        composed[0]["a"] = 99

        assert bootstrap[0]["a"] == 1


# ------------------------------------------------------- 지표를 섞지 않는다
class TestTheTwoMetricsStaySeparate:
    def test_bootstrap_validation_is_reported(self, models):
        report = retrain(history=[], bootstrap_path=BOOTSTRAP,
                         models_directory=models)

        assert report.bootstrap_validation.mae_sec is not None
        assert report.bootstrap_validation.mape_percent is not None

    def test_it_says_what_it_measures(self, models):
        report = retrain(history=[], bootstrap_path=BOOTSTRAP,
                         models_directory=models)

        assert "pipeline" in report.bootstrap_validation.note

    def test_a_handful_of_measured_rows_is_insufficient(self, models):
        report = retrain(history=many_records(5), bootstrap_path=BOOTSTRAP,
                         models_directory=models)

        assert report.measured_validation.mae_sec is None
        assert INSUFFICIENT_DATA in report.measured_validation.note

    def test_enough_measured_rows_are_scored(self, models):
        report = retrain(history=many_records(MIN_MEASURED_FOR_VALIDATION),
                         bootstrap_path=BOOTSTRAP, models_directory=models)

        assert report.measured_validation.measured
        assert report.measured_validation.mae_sec >= 0
        assert report.measured_validation.mape_percent >= 0

    def test_the_measured_score_says_it_is_a_fit_check(self, models):
        # 그 행들은 학습에도 들어갔다. held-out 점수라고 부르면 안 된다.
        report = retrain(history=many_records(MIN_MEASURED_FOR_VALIDATION),
                         bootstrap_path=BOOTSTRAP, models_directory=models)

        assert "fit check" in report.measured_validation.note

    def test_the_two_are_not_the_same_number(self, models):
        report = retrain(history=many_records(MIN_MEASURED_FOR_VALIDATION),
                         bootstrap_path=BOOTSTRAP, models_directory=models)

        assert (report.bootstrap_validation.mae_sec
                != report.measured_validation.mae_sec)

    def test_both_are_written_to_the_metadata_separately(self, models):
        report = retrain(history=many_records(3), bootstrap_path=BOOTSTRAP,
                         models_directory=models)
        metadata = json.loads(
            report.training.metadata_path.read_text(encoding="utf-8"))

        assert "bootstrapValidation" in metadata
        assert "measuredValidation" in metadata
        assert metadata["measuredValidation"]["maeSec"] is None

    def test_the_baselines_are_the_existing_ones(self, models):
        report = retrain(history=[], bootstrap_path=BOOTSTRAP,
                         models_directory=models)
        metadata = json.loads(
            report.training.metadata_path.read_text(encoding="utf-8"))

        names = [score["name"] for score in metadata["baselines"]["scores"]]
        assert names == ["median", "throughput", "xgboost"]


# ------------------------------------------------------- production 판정
class TestNothingIsCalledProductionReady:
    def test_a_bootstrap_heavy_candidate_is_not_ready(self, models):
        report = retrain(history=many_records(10), bootstrap_path=BOOTSTRAP,
                         models_directory=models)

        assert report.production_ready is False
        assert report.production_accuracy == NOT_VALIDATED

    def test_cross_gpu_is_pending_without_measured_validation(self, models):
        report = retrain(history=many_records(3), bootstrap_path=BOOTSTRAP,
                         models_directory=models)

        assert report.cross_gpu == PENDING

    def test_cross_gpu_is_pending_with_one_gpu_however_many_rows(self, models):
        report = retrain(history=many_records(MIN_MEASURED_FOR_VALIDATION),
                         bootstrap_path=BOOTSTRAP, models_directory=models)

        assert report.measured_gpu_models == [
            "NVIDIA GeForce RTX 4070 Laptop GPU"]
        assert report.cross_gpu == PENDING

    def test_synthetic_gpu_variety_does_not_count(self, models):
        """부트스트랩 생성기는 GPU 다섯 종류를 만든다. 증거가 아니다."""
        report = retrain(history=many_records(3), bootstrap_path=BOOTSTRAP,
                         models_directory=models)
        metadata = json.loads(
            report.training.metadata_path.read_text(encoding="utf-8"))

        assert len(metadata["categoryMappings"]["gpu_model"]) >= 5
        assert len(metadata["measuredGpuModels"]) == 1

    def test_cross_gpu_passes_only_with_measured_variety(self, models):
        report = retrain(
            history=many_records(
                MIN_MEASURED_FOR_VALIDATION,
                gpus=("NVIDIA GeForce RTX 4070 Laptop GPU",
                      "NVIDIA GeForce RTX 4090")),
            bootstrap_path=BOOTSTRAP, models_directory=models)

        assert len(report.measured_gpu_models) == 2
        assert report.cross_gpu == "PASS"

    def test_the_limitations_say_why(self, models):
        report = retrain(history=many_records(3), bootstrap_path=BOOTSTRAP,
                         models_directory=models)
        text = " ".join(report.limitations)

        assert INSUFFICIENT_DATA in text
        assert "Cross-GPU" in text
        assert "BOOTSTRAP" in text

    def test_rejected_history_is_mentioned(self, models):
        report = retrain(
            history=many_records(3) + [a_record("9", status="CANCELLED")],
            bootstrap_path=BOOTSTRAP, models_directory=models)
        text = " ".join(report.limitations)

        assert "rejected" in text
        assert "not a completion time" in text


# ------------------------------------------------------- 기록의 흔적
class TestTheMetadataExplainsItself:
    def test_the_counts_are_all_there(self, models):
        report = retrain(
            history=many_records(3) + [a_record("9", status="FAILED")],
            bootstrap_path=BOOTSTRAP, models_directory=models)
        metadata = json.loads(
            report.training.metadata_path.read_text(encoding="utf-8"))

        assert metadata["bootstrapRowCount"] == 1200
        assert metadata["measuredRowCount"] == 3
        assert metadata["rejectedMeasuredRowCount"] == 1
        assert metadata["historyStatusCounts"] == {"COMPLETED": 3, "FAILED": 1}

    def test_the_history_range_is_recorded(self, models):
        report = retrain(history=many_records(3), bootstrap_path=BOOTSTRAP,
                         models_directory=models)
        metadata = json.loads(
            report.training.metadata_path.read_text(encoding="utf-8"))

        assert metadata["measuredHistoryRange"]["first"] is not None

    def test_the_training_data_kind_reflects_the_mix(self, models):
        report = retrain(history=many_records(3), bootstrap_path=BOOTSTRAP,
                         models_directory=models)
        metadata = json.loads(
            report.training.metadata_path.read_text(encoding="utf-8"))

        assert metadata["trainingDataType"] == "mixed-bootstrap-measured"

    def test_a_measured_only_run_says_so(self, models, tmp_path):
        empty = tmp_path / "no-bootstrap.jsonl"

        report = retrain(history=many_records(MIN_MEASURED_FOR_VALIDATION),
                         bootstrap_path=empty, models_directory=models)
        metadata = json.loads(
            report.training.metadata_path.read_text(encoding="utf-8"))

        assert report.bootstrap_row_count == 0
        assert metadata["trainingDataType"] == "measured-history"
        assert metadata["trainingDataKind"] == "MEASURED"

    def test_a_measured_only_run_can_be_production_ready(self, models, tmp_path):
        """합성이 한 줄도 없으면 그 판정은 참이 될 수 있다.

        그래도 ``productionAccuracy`` 는 cross-GPU까지 봐야 VALIDATED가 된다 --
        두 값이 따로 있는 이유다.
        """
        empty = tmp_path / "no-bootstrap.jsonl"

        report = retrain(history=many_records(MIN_MEASURED_FOR_VALIDATION),
                         bootstrap_path=empty, models_directory=models)

        assert report.production_ready is True
        assert report.production_accuracy == NOT_VALIDATED
        assert report.cross_gpu == PENDING


# ------------------------------------------------------- leakage
class TestNoOutcomeReachesTheTrainer:
    def test_the_candidate_was_trained_on_features_and_target_only(self, models):
        report = retrain(history=many_records(3), bootstrap_path=BOOTSTRAP,
                         models_directory=models)
        metadata = json.loads(
            report.training.metadata_path.read_text(encoding="utf-8"))

        assert metadata["featureNames"] == list(RUNTIME_FEATURE_NAMES)
        assert "predicted_runtime_sec" not in metadata["featureNames"]
        assert "status" not in metadata["featureNames"]

    def test_a_history_full_of_predictions_still_trains_clean(self, models):
        history = [a_record(str(3000 + n), predicted_runtime_sec=999.0)
                   for n in range(3)]

        report = retrain(history=history, bootstrap_path=BOOTSTRAP,
                         models_directory=models)

        assert report.measured_row_count == 3
