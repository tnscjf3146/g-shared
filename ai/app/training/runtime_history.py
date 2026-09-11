"""끝난 Job을 학습 행으로 바꾸는 곳.

예측기는 지금 합성 데이터로만 학습돼 있다. 이 모듈은 그것을 실제 실행 기록으로
바꿔 나갈 입구이고, 하는 일은 대부분 **거절하는 것**이다.

거절이 기본값인 이유는 target 때문이다. ``actual_runtime_sec`` 는 "이 작업이 이
GPU에서 얼마나 걸리는가"라는 질문의 답이어야 하는데, 끝난 Job의 그 숫자가 항상
그 질문의 답인 것은 아니다.

* **CANCELLED** — 고객이 중간에 멈춘 시간이다. System 10이 이 값을 실측
  monotonic으로 정확히 기록하지만, 그것은 "72%에서 멈추기까지 걸린 시간"이지
  완주 시간이 아니다. 학습에 넣으면 모델은 모든 작업이 실제보다 빨리 끝난다고
  배운다.
* **FAILED** — CUDA OOM으로 40초 만에 죽은 Job의 40초는 그 작업의 실행 시간이
  아니라 죽기까지 걸린 시간이다.

그래서 ``COMPLETED`` 만 받는다. 나머지는 버리는 것이 아니라 이유와 함께 세어서
보고한다 -- 몇 건이 왜 빠졌는지 모르는 채로 재학습하는 것이 더 나쁘다.

**Feature에는 결과가 들어가지 않는다.** :func:`to_training_rows` 가 만드는 행에는
11개 feature와 target뿐이고, ``predicted_runtime_sec`` · ``status`` ·
``runtime_model_version`` · ``job_id`` 는 없다. 예측값을 feature로 넣으면 모델은
예측기를 흉내 내는 법을 배우고 검증 오차는 아름다워진다.

**Backend에 붙지 않는다.** :class:`RuntimeHistorySource` 는 추상이고 구현은 파일
하나뿐이다. AI가 MySQL에 직접 붙는 구조는 만들지 않았다 --
``docs/runtime-history-contract.md`` 에 Backend가 내주어야 할 최소 계약을 적어
두었다.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

from app.scheduler.runtime_features import (
    CATEGORICAL_FEATURE_NAMES,
    RUNTIME_FEATURE_NAMES,
    TARGET_NAME,
)

from .provenance import PROVENANCE_FIELD, Provenance

#: 학습 대상이 될 수 있는 유일한 상태.
COMPLETED = "COMPLETED"

#: 기록에는 있지만 feature가 되어서는 안 되는 열.
#:
#: 셋은 결과(target leakage)이고, 나머지는 식별자·시각이다. 식별자는 그 자체로
#: 시간 순서를 담고 있어서 -- job_id가 클수록 최근이다 -- 모델이 "최근 Job은
#: 빠르다" 같은 것을 배울 수 있다.
NEVER_A_FEATURE = frozenset({
    TARGET_NAME,
    "predicted_runtime_sec",
    "status",
    "runtime_model_version",
    "job_id",
    "worker_id",
    "completed_at",
    "result_hash",
    "fail_reason",
})


class RejectReason(str, Enum):
    """왜 이 기록이 학습에 들어가지 않았는지."""

    NOT_COMPLETED = "NOT_COMPLETED"
    MISSING_FEATURE = "MISSING_FEATURE"
    BAD_FEATURE = "BAD_FEATURE"
    BAD_TARGET = "BAD_TARGET"
    #: 같은 jobId가 두 번 나왔고 내용이 다르다. 어느 쪽이 맞는지 알 수 없다.
    CONFLICTING_DUPLICATE = "CONFLICTING_DUPLICATE"
    NOT_AN_OBJECT = "NOT_AN_OBJECT"


@dataclass(frozen=True)
class Rejection:
    """버려진 기록 하나와 그 이유. 개수만 세지 않고 이유를 남긴다."""

    job_id: str
    reason: RejectReason
    detail: str


@dataclass(frozen=True)
class RuntimeHistoryRecord:
    """끝난 Job 하나. 11개 feature와 결과, 그리고 출처.

    결과 쪽 값들은 **학습에 쓰이지 않는다.** 여기 있는 이유는 재학습 뒤에
    "이전 모델은 이 Job을 얼마로 예측했는가"를 사람이 볼 수 있어야 하기
    때문이고, :func:`to_training_rows` 는 이 중 아무것도 내보내지 않는다.
    """

    job_id: str
    worker_id: str
    features: Mapping[str, Any]
    actual_runtime_sec: float
    predicted_runtime_sec: float | None = None
    runtime_model_version: str | None = None
    status: str = COMPLETED
    completed_at: str | None = None
    provenance: Provenance = Provenance.MEASURED

    @property
    def gpu_model(self) -> str:
        return str(self.features.get("gpu_model", ""))

    def same_as(self, other: "RuntimeHistoryRecord") -> bool:
        """같은 Job의 같은 기록인가. 중복 제거의 기준."""
        return (dict(self.features) == dict(other.features)
                and self.actual_runtime_sec == other.actual_runtime_sec)


@dataclass(frozen=True)
class HistoryReadResult:
    """읽은 결과 전부. 받은 것과 버린 것과 그 이유."""

    records: list[RuntimeHistoryRecord]
    rejections: list[Rejection]
    status_counts: dict[str, int]

    @property
    def measured_row_count(self) -> int:
        return len(self.records)

    @property
    def rejected_row_count(self) -> int:
        return len(self.rejections)

    @property
    def gpu_models(self) -> list[str]:
        """실측 행에 실제로 나온 GPU. **합성 행의 것은 절대 섞지 않는다.**"""
        return sorted({record.gpu_model for record in self.records
                       if record.gpu_model})

    def reasons(self) -> dict[str, int]:
        return dict(sorted(Counter(
            rejection.reason.value for rejection in self.rejections).items()))


class RuntimeHistorySource(Protocol):
    """끝난 Job을 내주는 무언가.

    지금 구현은 파일 하나뿐이다. Backend가 이력 API를 갖게 되면 그것을 부르는
    구현이 하나 더 생기고, 이 모듈의 나머지는 그대로다 -- 그 경계를 위해 있는
    추상이다. AI가 Backend의 MySQL에 직접 붙는 구현은 만들지 않는다.
    """

    def records(self) -> Iterable[Mapping[str, Any]]:
        """가공되지 않은 기록들. 검사는 :func:`read_history` 가 한다."""


class FileHistorySource:
    """JSONL 파일 하나. 한 줄에 Job 하나."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def records(self) -> list[Mapping[str, Any]]:
        if not self._path.is_file():
            return []
        rows: list[Mapping[str, Any]] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text:
                continue
            rows.append(json.loads(text))
        return rows


def read_history(
    rows: Sequence[Mapping[str, Any]] | RuntimeHistorySource,
) -> HistoryReadResult:
    """기록들을 검사하고, 쓸 수 있는 것과 버린 것으로 나눈다.

    실패하지 않는다. 한 줄이 잘못됐다고 재학습 전체를 멈추면, 이력 파일이
    커질수록 아무도 재학습을 못 하게 된다. 대신 무엇이 왜 빠졌는지를 전부
    돌려주고, 부르는 쪽이 그 숫자를 보고 판단한다.
    """
    if hasattr(rows, "records"):
        rows = list(rows.records())

    records: list[RuntimeHistoryRecord] = []
    rejections: list[Rejection] = []
    statuses: Counter = Counter()
    seen: dict[str, RuntimeHistoryRecord] = {}

    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            rejections.append(Rejection(
                f"row {index}", RejectReason.NOT_AN_OBJECT,
                "the record is not a JSON object"))
            continue

        job_id = str(row.get("job_id", f"row {index}"))
        status = str(row.get("status", "")).upper()
        statuses[status or "UNKNOWN"] += 1

        if status != COMPLETED:
            rejections.append(Rejection(
                job_id, RejectReason.NOT_COMPLETED,
                f"status is {status or 'missing'}, not {COMPLETED}"))
            continue

        built = _record_from(job_id, row)
        if isinstance(built, Rejection):
            rejections.append(built)
            continue

        already = seen.get(job_id)
        if already is not None:
            if already.same_as(built):
                # 같은 Job이 두 번 실린 것. 내용이 같으니 한 건으로 센다.
                continue
            rejections.append(Rejection(
                job_id, RejectReason.CONFLICTING_DUPLICATE,
                "the same job appears twice with different values"))
            continue

        seen[job_id] = built
        records.append(built)

    return HistoryReadResult(records, rejections, dict(sorted(statuses.items())))


def _record_from(job_id: str, row: Mapping[str, Any]) -> Any:
    """한 줄을 기록으로. 실패하면 :class:`Rejection`."""
    features: dict[str, Any] = {}
    for name in RUNTIME_FEATURE_NAMES:
        if name not in row or row[name] is None:
            return Rejection(job_id, RejectReason.MISSING_FEATURE,
                             f"{name} is missing")
        value = row[name]
        if name in CATEGORICAL_FEATURE_NAMES:
            text = str(value).strip()
            if not text:
                return Rejection(job_id, RejectReason.BAD_FEATURE,
                                 f"{name} is empty")
            features[name] = text
            continue
        number = _number(value)
        if number is None:
            return Rejection(job_id, RejectReason.BAD_FEATURE,
                             f"{name} is not a usable number")
        features[name] = number

    target = _number(row.get(TARGET_NAME))
    if target is None:
        return Rejection(job_id, RejectReason.BAD_TARGET,
                         f"{TARGET_NAME} is not a usable number")
    if target <= 0:
        # 0초에 끝난 학습은 없다. MAPE의 분모가 되기도 한다.
        return Rejection(job_id, RejectReason.BAD_TARGET,
                         f"{TARGET_NAME} must be greater than zero")

    provenance = row.get(PROVENANCE_FIELD, Provenance.MEASURED.value)
    try:
        source = Provenance(str(provenance))
    except ValueError:
        return Rejection(job_id, RejectReason.BAD_FEATURE,
                         f"{PROVENANCE_FIELD} is not a known source")

    return RuntimeHistoryRecord(
        job_id=job_id,
        worker_id=str(row.get("worker_id", "")),
        features=features,
        actual_runtime_sec=target,
        predicted_runtime_sec=_number(row.get("predicted_runtime_sec")),
        runtime_model_version=_text(row.get("runtime_model_version")),
        status=COMPLETED,
        completed_at=_text(row.get("completed_at")),
        provenance=source,
    )


def to_training_rows(
    records: Sequence[RuntimeHistoryRecord],
) -> list[dict[str, Any]]:
    """학습기가 먹는 모양으로. **11개 feature와 target과 출처뿐이다.**

    기록에 무엇이 더 들어 있든 여기서 다시 만들어 내보낸다. 기록을 그대로
    넘기면 ``predicted_runtime_sec`` 가 따라 들어가고, 그러면 모델은 실행
    시간을 배우는 대신 예측기를 흉내 내는 법을 배운다 -- 검증 오차는
    아름다워지고 실제 예측은 나아지지 않는다.
    """
    rows: list[dict[str, Any]] = []
    for record in records:
        row = {name: record.features[name] for name in RUNTIME_FEATURE_NAMES}
        row[TARGET_NAME] = record.actual_runtime_sec
        row[PROVENANCE_FIELD] = record.provenance.value
        rows.append(row)
    return rows


def check_no_leakage(rows: Sequence[Mapping[str, Any]]) -> None:
    """학습 행에 결과가 섞여 들어가지 않았는지. 섞였으면 멈춘다.

    :func:`to_training_rows` 가 이미 그것만 만들지만, 다른 경로로 만들어진
    행이 학습기에 도달할 수 있다. 이 검사는 싸고, 놓쳤을 때의 대가는 조용히
    쓸모없는 모델이다.
    """
    allowed = set(RUNTIME_FEATURE_NAMES) | {TARGET_NAME, PROVENANCE_FIELD}
    for index, row in enumerate(rows):
        leaked = sorted(set(row) & NEVER_A_FEATURE - {TARGET_NAME})
        if leaked:
            raise ValueError(
                f"row {index} carries outcome column(s) into training: "
                f"{', '.join(leaked)}")
        extra = sorted(set(row) - allowed)
        if extra:
            raise ValueError(
                f"row {index} carries unexpected column(s): {', '.join(extra)}")


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()
