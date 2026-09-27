"""끝난 Job이 학습 행이 되기까지 통과해야 하는 것들.

이 파일의 대부분은 **거절**에 관한 것이고, 그중 두 건이 이 단계의 존재 이유다.

``actual_runtime_sec`` 는 "이 작업이 이 GPU에서 얼마나 걸리는가"의 답이어야
한다. CANCELLED Job의 그 숫자는 고객이 72%에서 멈추기까지 걸린 시간이고, FAILED
Job의 것은 CUDA OOM으로 죽기까지 걸린 시간이다. 둘 다 실측이고 둘 다 정확하지만
둘 다 그 질문의 답이 아니다 -- 넣으면 모델은 모든 작업이 실제보다 빨리 끝난다고
배운다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.scheduler.runtime_features import (
    CATEGORICAL_FEATURE_NAMES,
    RUNTIME_FEATURE_NAMES,
    TARGET_NAME,
)
from app.training.provenance import PROVENANCE_FIELD, Provenance
from app.training.runtime_history import (
    COMPLETED,
    NEVER_A_FEATURE,
    FileHistorySource,
    RejectReason,
    check_no_leakage,
    read_history,
    to_training_rows,
)

FIXTURE = Path(__file__).parent / "fixtures" / "runtime_history_sample.jsonl"


def a_record(job_id="1", status=COMPLETED, **overrides):
    """완전한 COMPLETED 기록 하나. 테스트마다 필요한 곳만 바꾼다."""
    record = {
        "job_id": job_id,
        "worker_id": "worker-a",
        "status": status,
        "completed_at": "2026-09-01T10:00:00Z",
        "gpu_model": "NVIDIA GeForce RTX 4070 Laptop GPU",
        "vram_mb": 8188,
        "benchmark_tokens_per_sec": 29048.535,
        "sample_count": 240,
        "effective_tokens": 25536,
        "avg_seq_length": 81.812,
        "max_seq_length": 118,
        "epochs": 1,
        "batch_size": 16,
        "max_length": 512,
        "model_id": "3",
        "predicted_runtime_sec": 41.2,
        TARGET_NAME: 33.441,
        "runtime_model_version": "runtime-xgb-v1",
        PROVENANCE_FIELD: Provenance.MEASURED.value,
    }
    record.update(overrides)
    return record


class TestCompletedOnly:
    """완주한 Job만 target이 된다."""

    def test_a_completed_job_is_kept(self):
        read = read_history([a_record()])

        assert read.measured_row_count == 1
        assert read.rejected_row_count == 0

    def test_a_cancelled_job_is_rejected(self):
        """그 39.5초는 중단까지 걸린 시간이지 완주 시간이 아니다."""
        read = read_history([a_record(status="CANCELLED", **{TARGET_NAME: 39.5})])

        assert read.measured_row_count == 0
        assert read.rejections[0].reason is RejectReason.NOT_COMPLETED

    def test_a_failed_job_is_rejected(self):
        read = read_history([a_record(status="FAILED", **{TARGET_NAME: 41.2})])

        assert read.measured_row_count == 0
        assert read.rejections[0].reason is RejectReason.NOT_COMPLETED

    @pytest.mark.parametrize(
        "status", ["RUNNING", "ASSIGNED", "CANCELLING", "FUNDED", "CREATED", ""])
    def test_nothing_else_is_trained_on_either(self, status):
        read = read_history([a_record(status=status)])

        assert read.measured_row_count == 0

    def test_the_statuses_are_counted_not_just_dropped(self):
        # 몇 건이 왜 빠졌는지 모르는 채로 재학습하는 것이 더 나쁘다.
        read = read_history([
            a_record("1"), a_record("2", status="CANCELLED"),
            a_record("3", status="FAILED"), a_record("4", status="FAILED"),
        ])

        assert read.status_counts == {"CANCELLED": 1, "COMPLETED": 1, "FAILED": 2}
        assert read.reasons() == {"NOT_COMPLETED": 3}


class TestTheTarget:
    @pytest.mark.parametrize("value", [None, 0, -1, -0.5])
    def test_a_runtime_that_is_not_a_duration_is_rejected(self, value):
        read = read_history([a_record(**{TARGET_NAME: value})])

        assert read.measured_row_count == 0
        assert read.rejections[0].reason is RejectReason.BAD_TARGET

    def test_a_missing_runtime_is_rejected(self):
        record = a_record()
        del record[TARGET_NAME]

        assert read_history([record]).measured_row_count == 0

    def test_nan_and_infinity_are_rejected(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            read = read_history([a_record(**{TARGET_NAME: value})])
            assert read.measured_row_count == 0

    def test_a_boolean_is_not_a_runtime(self):
        # True == 1 in Python, which is how it would slip through.
        read = read_history([a_record(**{TARGET_NAME: True})])

        assert read.measured_row_count == 0

    def test_a_string_is_not_a_runtime(self):
        read = read_history([a_record(**{TARGET_NAME: "33.441"})])

        assert read.measured_row_count == 0


class TestTheElevenFeatures:
    @pytest.mark.parametrize("name", list(RUNTIME_FEATURE_NAMES))
    def test_every_one_of_them_is_required(self, name):
        record = a_record()
        del record[name]

        read = read_history([record])

        assert read.measured_row_count == 0
        assert read.rejections[0].reason is RejectReason.MISSING_FEATURE
        assert name in read.rejections[0].detail

    @pytest.mark.parametrize("name", list(RUNTIME_FEATURE_NAMES))
    def test_a_null_is_the_same_as_missing(self, name):
        read = read_history([a_record(**{name: None})])

        assert read.measured_row_count == 0

    @pytest.mark.parametrize(
        "name", [n for n in RUNTIME_FEATURE_NAMES
                 if n not in CATEGORICAL_FEATURE_NAMES])
    def test_a_numeric_feature_that_is_not_a_number_is_rejected(self, name):
        read = read_history([a_record(**{name: "many"})])

        assert read.measured_row_count == 0
        assert read.rejections[0].reason is RejectReason.BAD_FEATURE

    @pytest.mark.parametrize("name", list(CATEGORICAL_FEATURE_NAMES))
    def test_an_empty_category_is_rejected(self, name):
        read = read_history([a_record(**{name: "   "})])

        assert read.measured_row_count == 0

    def test_nothing_is_filled_in_for_a_missing_value(self):
        """기본값으로 채우면 그 행은 통과하고 모델은 틀린 것을 배운다."""
        record = a_record()
        del record["benchmark_tokens_per_sec"]

        read = read_history([record])

        assert read.measured_row_count == 0

    def test_there_are_exactly_eleven(self):
        # 이번 작업에서 feature를 더하거나 빼지 않는다.
        assert len(RUNTIME_FEATURE_NAMES) == 11


class TestDuplicates:
    def test_the_same_job_twice_counts_once(self):
        read = read_history([a_record("1"), a_record("1")])

        assert read.measured_row_count == 1
        assert read.rejected_row_count == 0

    def test_the_same_job_with_a_different_runtime_is_a_conflict(self):
        """어느 쪽이 맞는지 알 수 없다. 고르지 않고 둘 다 쓰지 않는다."""
        read = read_history([
            a_record("1", **{TARGET_NAME: 33.4}),
            a_record("1", **{TARGET_NAME: 99.9}),
        ])

        assert read.measured_row_count == 1
        assert read.rejections[0].reason is RejectReason.CONFLICTING_DUPLICATE

    def test_the_same_job_with_different_features_is_a_conflict(self):
        read = read_history([a_record("1"), a_record("1", epochs=9)])

        assert read.rejections[0].reason is RejectReason.CONFLICTING_DUPLICATE

    def test_different_jobs_are_both_kept(self):
        read = read_history([a_record("1"), a_record("2")])

        assert read.measured_row_count == 2


class TestNoLeakage:
    """예측값을 feature로 넣으면 모델은 예측기를 흉내 내는 법을 배운다."""

    def test_a_training_row_carries_only_features_target_and_provenance(self):
        rows = to_training_rows(read_history([a_record()]).records)

        assert set(rows[0]) == set(RUNTIME_FEATURE_NAMES) | {
            TARGET_NAME, PROVENANCE_FIELD}

    @pytest.mark.parametrize(
        "name", sorted(NEVER_A_FEATURE - {TARGET_NAME}))
    def test_no_outcome_column_survives(self, name):
        rows = to_training_rows(read_history([a_record()]).records)

        assert name not in rows[0]

    def test_the_prediction_is_kept_on_the_record_but_never_trained_on(self):
        # 사람이 "이전 모델은 이 Job을 얼마로 예측했나"를 볼 수 있어야 한다.
        read = read_history([a_record()])

        assert read.records[0].predicted_runtime_sec == 41.2
        assert "predicted_runtime_sec" not in to_training_rows(read.records)[0]

    def test_the_guard_catches_a_row_built_some_other_way(self):
        leaky = {name: 1.0 for name in RUNTIME_FEATURE_NAMES}
        leaky.update({TARGET_NAME: 10.0, "predicted_runtime_sec": 9.0})

        with pytest.raises(ValueError, match="predicted_runtime_sec"):
            check_no_leakage([leaky])

    def test_the_guard_catches_an_unexpected_column(self):
        extra = {name: 1.0 for name in RUNTIME_FEATURE_NAMES}
        extra.update({TARGET_NAME: 10.0, "gpu_temperature": 71})

        with pytest.raises(ValueError, match="gpu_temperature"):
            check_no_leakage([extra])

    def test_a_clean_row_passes(self):
        check_no_leakage(to_training_rows(read_history([a_record()]).records))


class TestFeatureOrder:
    def test_training_rows_use_the_incumbent_order(self):
        """열 순서가 어긋난 모델은 조용히 틀린 답을 낸다."""
        from app.training.runtime_trainer import build_frame

        rows = to_training_rows(read_history([a_record()]).records)
        frame = build_frame(rows)

        assert list(frame.columns) == list(RUNTIME_FEATURE_NAMES)

    def test_the_incumbent_model_expects_that_same_order(self):
        metadata = json.loads(
            (Path(__file__).parents[1] / "models"
             / "runtime_model.metadata.json").read_text(encoding="utf-8"))

        assert metadata["featureNames"] == list(RUNTIME_FEATURE_NAMES)
        assert metadata["target"] == TARGET_NAME


class TestProvenance:
    def test_history_records_are_measured_by_default(self):
        record = a_record()
        del record[PROVENANCE_FIELD]

        read = read_history([record])

        assert read.records[0].provenance is Provenance.MEASURED

    def test_the_declared_source_is_kept(self):
        read = read_history([a_record(**{PROVENANCE_FIELD: "PRODUCTION"})])

        assert read.records[0].provenance is Provenance.PRODUCTION

    def test_an_unknown_source_is_rejected(self):
        read = read_history([a_record(**{PROVENANCE_FIELD: "GUESSED"})])

        assert read.measured_row_count == 0

    def test_it_travels_into_the_training_row(self):
        rows = to_training_rows(read_history([a_record()]).records)

        assert rows[0][PROVENANCE_FIELD] == Provenance.MEASURED.value

    def test_only_measured_gpus_are_counted(self):
        """합성 행의 GPU 다양성은 cross-GPU 검증의 증거가 아니다."""
        read = read_history([
            a_record("1", gpu_model="NVIDIA GeForce RTX 4070 Laptop GPU"),
            a_record("2", gpu_model="NVIDIA GeForce RTX 4090"),
            a_record("3", status="CANCELLED",
                     gpu_model="NVIDIA GeForce RTX 3060"),
        ])

        assert read.gpu_models == ["NVIDIA GeForce RTX 4070 Laptop GPU",
                                   "NVIDIA GeForce RTX 4090"]


class TestReadingAFile:
    def test_the_fixture_reads(self):
        read = read_history(FileHistorySource(FIXTURE))

        assert read.measured_row_count == 3
        assert read.rejected_row_count == 4

    def test_it_rejects_for_the_right_reasons(self):
        read = read_history(FileHistorySource(FIXTURE))

        assert read.reasons() == {
            "BAD_TARGET": 1,            # actual_runtime_sec: null
            "MISSING_FEATURE": 1,       # gpu_model 없음
            "NOT_COMPLETED": 2,         # CANCELLED, FAILED
        }

    def test_a_file_that_is_not_there_is_empty_not_an_error(self):
        read = read_history(FileHistorySource(Path("nowhere.jsonl")))

        assert read.measured_row_count == 0

    def test_blank_lines_are_skipped(self, tmp_path):
        path = tmp_path / "history.jsonl"
        path.write_text(json.dumps(a_record()) + "\n\n\n", encoding="utf-8")

        assert read_history(FileHistorySource(path)).measured_row_count == 1


class TestNothingRaises:
    def test_one_bad_record_does_not_stop_the_rest(self):
        """이력 파일이 커질수록 한 줄 때문에 재학습을 못 하게 되면 안 된다."""
        read = read_history([
            a_record("1"), "not an object", a_record("2"), 42,
            a_record("3", **{TARGET_NAME: -1}),
        ])

        assert read.measured_row_count == 2
        assert read.rejected_row_count == 3

    def test_an_empty_history_is_not_an_error(self):
        read = read_history([])

        assert read.measured_row_count == 0
        assert read.rejections == []
