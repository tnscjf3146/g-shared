"""실측 이력으로 예측기를 다시 학습한다 -- 후보까지만.

현역 모델(``models/runtime_model.json``)은 **건드리지 않는다.** 이 파이프라인이
만드는 것은 ``models/candidates/<version>/`` 아래의 후보 하나이고, 그것을 실제
예측에 쓸지는 사람이 숫자를 보고 결정한다.

자동 승격을 넣지 않은 이유는 지금 숫자로는 결정할 수 없기 때문이다. 후보가
현역보다 검증 오차가 낮게 나올 수 있고, 그 검증셋이 합성 데이터라면 그 숫자는
"XGBoost가 생성기의 공식을 얼마나 잘 외웠는가"일 뿐 실행 시간을 더 잘 맞힌다는
뜻이 아니다. 그래서 지표를 **두 벌로 나누어** 보고한다.

* **Bootstrap Validation** -- 합성 행으로 잰 것. 파이프라인이 도는지를 말한다.
* **Measured Validation** -- 실제 끝난 Job으로 잰 것. 이것만이 정확도다.
* **Cross-GPU** -- 실측 행이 GPU 두 종류 이상을 덮었는가. 합성 행의 GPU 다양성은
  증거가 아니다.

실측 행이 적으면 ``INSUFFICIENT_DATA`` 로 두고 ``productionReady`` 는 거짓으로
남는다. 근거 없는 문턱을 만들어 참으로 바꾸지 않는다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from app.scheduler.runtime_features import (
    RUNTIME_FEATURE_NAMES,
    TARGET_NAME,
    RuntimeFeatures,
)
from app.scheduler.runtime_predictor import load_runtime_predictor

from .provenance import PROVENANCE_FIELD, Provenance
from .runtime_dataset import read_rows
from .runtime_history import (
    HistoryReadResult,
    RuntimeHistorySource,
    check_no_leakage,
    read_history,
    to_training_rows,
)
from .runtime_trainer import (
    MIN_ROWS_FOR_VALIDATION,
    MODEL_VERSION,
    TrainingReport,
    train_runtime_model,
)

#: 후보가 사는 곳. 현역 옆이 아니라 아래다 -- 같은 디렉터리에 두면
#: :func:`load_runtime_predictor` 가 파일 이름으로 찾으므로 어느 쪽을 읽을지
#: 이름 하나에 달리게 된다.
CANDIDATES_DIR_NAME = "candidates"

#: 후보 이름. 현역과 절대 같지 않다.
CANDIDATE_VERSION = "runtime-xgb-v2-candidate"

#: 실측 검증을 말할 수 있는 최소 행 수.
#:
#: 새로 만든 숫자가 아니라 학습기가 이미 쓰는 :data:`MIN_ROWS_FOR_VALIDATION`
#: 이다. 같은 이유에서다 -- 이보다 적으면 검증셋을 떼어도 숫자만 나올 뿐 의미가
#: 없다. 재학습이라고 더 관대한 문턱을 새로 만들 이유가 없다.
MIN_MEASURED_FOR_VALIDATION = MIN_ROWS_FOR_VALIDATION

#: Cross-GPU를 말하려면 실측 행이 최소 이만큼의 GPU를 덮어야 한다.
MIN_GPUS_FOR_CROSS_GPU = 2

INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
NOT_VALIDATED = "NOT_VALIDATED"
PENDING = "PENDING"


@dataclass(frozen=True)
class Scored:
    """한 벌의 지표. 잴 수 없었으면 :attr:`note` 만 남는다."""

    row_count: int
    mae_sec: float | None = None
    mape_percent: float | None = None
    note: str = ""

    @property
    def measured(self) -> bool:
        return self.mae_sec is not None

    def as_payload(self) -> dict[str, Any]:
        return {
            "rowCount": self.row_count,
            "maeSec": self.mae_sec,
            "mapePercent": self.mape_percent,
            "note": self.note,
        }


@dataclass(frozen=True)
class RetrainingReport:
    """재학습 한 번의 결과 전부. 후보 경로와 두 벌의 지표와 남은 한계."""

    candidate_version: str
    candidate_directory: Path
    incumbent_version: str
    incumbent_untouched: bool
    bootstrap_row_count: int
    measured_row_count: int
    rejected_row_count: int
    rejection_reasons: dict[str, int]
    status_counts: dict[str, int]
    measured_gpu_models: list[str]
    training: TrainingReport
    bootstrap_validation: Scored
    measured_validation: Scored
    cross_gpu: str
    production_ready: bool
    production_accuracy: str
    limitations: list[str] = field(default_factory=list)

    @property
    def candidate_loads(self) -> bool:
        return self.incumbent_untouched


def compose(
    bootstrap_rows: Sequence[Mapping[str, Any]],
    measured_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """합성 행과 실측 행을 한 학습셋으로. 출처는 그대로 따라간다.

    합성 행을 지우지 않는다. 실측이 1,200건을 넘기 전까지는 그것만으로 모델이
    나오지 않고, 섞여 있다는 사실은 ``provenance`` 가 행마다 들고 있으므로
    metadata와 ``productionReady`` 에 그대로 반영된다.
    """
    composed = [dict(row) for row in bootstrap_rows]
    composed.extend(dict(row) for row in measured_rows)
    return composed


def measured_only(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """실측·실운영 행만. 지표를 섞지 않기 위한 것."""
    real = {Provenance.MEASURED.value, Provenance.PRODUCTION.value}
    return [dict(row) for row in rows
            if str(row.get(PROVENANCE_FIELD, "")) in real]


def retrain(
    *,
    history: RuntimeHistorySource | Sequence[Mapping[str, Any]],
    bootstrap_path: Path,
    models_directory: Path,
    candidate_version: str = CANDIDATE_VERSION,
) -> RetrainingReport:
    """이력을 읽고, 후보를 만들고, 그것이 실제로 읽히는지 확인한다.

    현역 모델 파일은 읽지도 쓰지도 않는다 -- 후보는 하위 디렉터리에 저장되고,
    :func:`load_runtime_predictor` 를 그 디렉터리로 불러 다시 읽어 본다. 저장은
    됐는데 읽을 수 없는 후보는 후보가 아니다.
    """
    read = read_history(history)
    measured_rows = to_training_rows(read.records)
    check_no_leakage(measured_rows)

    bootstrap_rows = read_rows(bootstrap_path) if bootstrap_path.is_file() else []
    composed = compose(bootstrap_rows, measured_rows)

    candidate_directory = (models_directory / CANDIDATES_DIR_NAME
                           / candidate_version)
    incumbent_before = _fingerprint(models_directory)

    training = train_runtime_model(
        composed,
        models_directory=candidate_directory,
        training_data_type=_data_type(len(bootstrap_rows), len(measured_rows)),
        model_version=candidate_version,
    )

    untouched = _fingerprint(models_directory) == incumbent_before
    bootstrap_validation = Scored(
        row_count=training.validation_rows,
        mae_sec=training.mae_sec,
        mape_percent=training.mape_percent,
        note=("mixed validation set; bootstrap rows dominate it, so this "
              "measures the pipeline rather than production accuracy"
              if bootstrap_rows else
              "measured validation set"),
    )
    measured_validation = _score_measured(candidate_directory, measured_rows)
    cross_gpu = _cross_gpu(read, measured_validation)

    metadata = json.loads(training.metadata_path.read_text(encoding="utf-8"))
    production_ready = bool(metadata.get("productionReady"))
    limitations = list(metadata.get("limitations") or [])
    limitations.extend(_extra_limitations(measured_validation, cross_gpu, read))

    metadata.update({
        "incumbentModelVersion": MODEL_VERSION,
        "featureCount": len(RUNTIME_FEATURE_NAMES),
        "bootstrapRowCount": len(bootstrap_rows),
        "measuredRowCount": len(measured_rows),
        "rejectedMeasuredRowCount": read.rejected_row_count,
        "rejectionReasons": read.reasons(),
        "historyStatusCounts": read.status_counts,
        "measuredGpuModels": read.gpu_models,
        "measuredHistoryRange": _history_range(read),
        "bootstrapValidation": bootstrap_validation.as_payload(),
        "measuredValidation": measured_validation.as_payload(),
        "crossGpuValidation": cross_gpu,
        "productionAccuracy": (
            "VALIDATED" if measured_validation.measured and cross_gpu == "PASS"
            else NOT_VALIDATED),
        "promoted": False,
        "limitations": limitations,
    })
    training.metadata_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

    return RetrainingReport(
        candidate_version=candidate_version,
        candidate_directory=candidate_directory,
        incumbent_version=MODEL_VERSION,
        incumbent_untouched=untouched,
        bootstrap_row_count=len(bootstrap_rows),
        measured_row_count=len(measured_rows),
        rejected_row_count=read.rejected_row_count,
        rejection_reasons=read.reasons(),
        status_counts=read.status_counts,
        measured_gpu_models=read.gpu_models,
        training=training,
        bootstrap_validation=bootstrap_validation,
        measured_validation=measured_validation,
        cross_gpu=cross_gpu,
        production_ready=production_ready,
        production_accuracy=str(metadata["productionAccuracy"]),
        limitations=limitations,
    )


def verify_candidate(candidate_directory: Path,
                     rows: Sequence[Mapping[str, Any]]) -> tuple[float, ...]:
    """후보를 **실제 예측기 로더로** 다시 읽고 답을 받아 본다.

    저장 직후에 하는 이유는, 여기서 걸리는 것들이 저장할 때는 걸리지 않기
    때문이다: feature 스키마가 어긋난 metadata, 카테고리 표가 빠진 파일, 읽히지만
    NaN을 내놓는 모델. 로더는 세 가지를 모두 거절하고, 그것이 이 함수가 로더를
    직접 쓰는 이유다.
    """
    predictor = load_runtime_predictor(candidate_directory)
    return predictor.predict([_features_of(row) for row in rows])


def _features_of(row: Mapping[str, Any]) -> RuntimeFeatures:
    """학습 행을 예측 입력으로. 11개 열만 꺼낸다.

    ``RuntimeFeatures`` 를 거쳐 가는 것이 요점이다. dict를 그대로 넘기면
    예측기가 아니라 이 함수가 열 순서를 정하게 되고, 그러면 학습 때와 다른
    순서로 넣어도 아무 데서도 걸리지 않는다.
    """
    return RuntimeFeatures(
        **{name: row[name] for name in RUNTIME_FEATURE_NAMES})


# ----------------------------------------------------------------- 내부
def _score_measured(candidate_directory: Path,
                    measured_rows: Sequence[Mapping[str, Any]]) -> Scored:
    """실측 행만으로 후보를 재 본다. 적으면 재지 않는다.

    합성 행이 섞인 검증셋의 숫자를 실측 정확도라고 부르지 않기 위해 따로
    계산한다. 행이 적을 때 굳이 계산해서 숫자를 내면 그 숫자가 인용되고, 인용된
    뒤에는 몇 건으로 잰 것인지 아무도 다시 보지 않는다.
    """
    if len(measured_rows) < MIN_MEASURED_FOR_VALIDATION:
        return Scored(
            row_count=len(measured_rows),
            note=(f"{INSUFFICIENT_DATA}: {len(measured_rows)} measured rows, "
                  f"fewer than the {MIN_MEASURED_FOR_VALIDATION} a validation "
                  "set needs to mean anything"),
        )

    predicted = np.asarray(verify_candidate(candidate_directory, measured_rows),
                           dtype="float64")
    actual = np.asarray([float(row[TARGET_NAME]) for row in measured_rows],
                        dtype="float64")
    mae = float(np.mean(np.abs(actual - predicted)))
    mape = float(np.mean(np.abs((actual - predicted) / actual)) * 100.0)
    return Scored(
        row_count=len(measured_rows), mae_sec=mae, mape_percent=mape,
        note=("measured from real completed jobs; these rows were also in "
              "training, so this is a fit check rather than a held-out score"),
    )


def _cross_gpu(read: HistoryReadResult, measured: Scored) -> str:
    """실측 행이 GPU 여러 종류를 덮었는가.

    합성 행의 GPU는 세지 않는다. 부트스트랩 생성기는 다섯 종류를 만들어 내므로
    그것을 세면 cross-GPU 검증을 한 적이 없는데도 통과해 버린다.
    """
    if not measured.measured:
        return PENDING
    if len(read.gpu_models) < MIN_GPUS_FOR_CROSS_GPU:
        return PENDING
    return "PASS"


def _extra_limitations(measured: Scored, cross_gpu: str,
                       read: HistoryReadResult) -> list[str]:
    notes: list[str] = []
    if not measured.measured:
        notes.append(
            f"Measured validation is {INSUFFICIENT_DATA} "
            f"({measured.row_count} rows). Production accuracy is "
            f"{NOT_VALIDATED} and this candidate must not be promoted on the "
            "bootstrap numbers alone.")
    if cross_gpu != "PASS":
        notes.append(
            "Cross-GPU accuracy is PENDING: the measured rows cover "
            f"{len(read.gpu_models)} GPU model(s). Synthetic GPU variety is "
            "not evidence.")
    if read.rejected_row_count:
        notes.append(
            f"{read.rejected_row_count} history record(s) were rejected "
            f"({read.reasons()}). Cancelled and failed jobs are excluded on "
            "purpose -- their runtime is not a completion time.")
    return notes


def _data_type(bootstrap: int, measured: int) -> str:
    if bootstrap and measured:
        return "mixed-bootstrap-measured"
    if measured:
        return "measured-history"
    return "synthetic-bootstrap"


def _history_range(read: HistoryReadResult) -> dict[str, str | None]:
    stamps = sorted(record.completed_at for record in read.records
                    if record.completed_at)
    return {"first": stamps[0] if stamps else None,
            "last": stamps[-1] if stamps else None}


def _fingerprint(directory: Path) -> dict[str, tuple[int, int]]:
    """현역 모델 파일들의 크기와 수정 시각. 건드리지 않았음을 보이기 위한 것."""
    from .runtime_trainer import METADATA_FILE_NAME, MODEL_FILE_NAME

    seen: dict[str, tuple[int, int]] = {}
    for name in (MODEL_FILE_NAME, METADATA_FILE_NAME):
        path = directory / name
        if path.is_file():
            stat = path.stat()
            seen[name] = (stat.st_size, stat.st_mtime_ns)
    return seen
