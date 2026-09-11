"""어떤 행이 어디에서 왔는가. 학습에 쓸 수 있는지는 여기서만 정한다.

이 파일이 있는 이유는 하나다. **측정하지 않은 값을 ``actual_runtime_sec`` 라는
이름으로 저장하면, 나중에 그것이 측정값이 아니었다는 사실을 알 방법이 없다.**
행 안에는 숫자만 남고, 그 숫자는 진짜와 똑같이 생겼다. 그래서 출처를 행마다
붙이고, 학습 코드가 그 표시를 읽어 스스로 거르게 한다 -- 주석이나 규율이 아니라
코드가 막는다.

네 가지 출처
------------

``MEASURED``
    실제 GPU에서 **끝까지** 돌린 Job. 지금 있는 것은 두 건뿐이다.

``CALIBRATION``
    ``--max-steps`` 같은 제한을 걸고 중간에서 끊은 실행. 속도를 재기 위한 것이고
    **완료된 Job이 아니다.** 이것이 이 모듈의 핵심이다: 11개 feature 안에는
    "몇 step에서 끊었는가"가 없다. 40-step calibration 행과 같은 데이터셋을 끝까지
    돌린 행은 **feature가 완전히 똑같고 target만 다르다.** 학습에 넣으면 모델은
    구분할 수 없는 두 답을 배우게 되고, 그 모순은 조용히 전체 예측을 망가뜨린다.
    그래서 기록은 남기되 학습 target으로는 **절대** 쓰지 않는다.

``BOOTSTRAP``
    파이프라인을 돌려 보려고 만든 합성값. 학습에 쓸 수 있지만 이것으로 학습한
    모델은 production이 아니다.

``PRODUCTION``
    앞으로 Backend ``job_results.actual_runtime_sec`` 에서 모일 실제 기록.
    아직 한 건도 없다.
"""

from __future__ import annotations

from collections import Counter
from enum import Enum
from typing import Mapping, Sequence

#: 행에 붙는 열 이름.
PROVENANCE_FIELD = "provenance"


class Provenance(str, Enum):
    MEASURED = "MEASURED"
    CALIBRATION = "CALIBRATION"
    BOOTSTRAP = "BOOTSTRAP"
    PRODUCTION = "PRODUCTION"


#: 학습 target으로 쓸 수 있는 출처.
#:
#: ``BOOTSTRAP`` 이 여기 있는 것은 타협이 아니라 현재 상태다 -- 실제 기록이 두
#: 건뿐이라 그것만으로는 모델이 나오지 않는다. 대신 bootstrap이 한 줄이라도 섞이면
#: :func:`is_production_ready` 가 거짓이 된다.
TRAINABLE = frozenset({
    Provenance.MEASURED, Provenance.PRODUCTION, Provenance.BOOTSTRAP})

#: 이것으로만 학습한 모델이라야 production을 말할 수 있다.
REAL = frozenset({Provenance.MEASURED, Provenance.PRODUCTION})


class ProvenanceError(ValueError):
    """행의 출처가 없거나, 학습에 쓸 수 없는 출처다."""


def provenance_of(row: Mapping[str, object], index: int = 0) -> Provenance:
    """행의 출처. 없으면 거절한다 -- 기본값을 주지 않는다.

    표시가 없는 행을 ``BOOTSTRAP`` 으로 가정하면 실측 데이터가 조용히 합성으로
    기록되고, ``MEASURED`` 로 가정하면 합성 데이터가 실측으로 둔갑한다. 둘 다
    나중에 되돌릴 수 없으므로 묻는 쪽이 답하게 한다.
    """
    if PROVENANCE_FIELD not in row:
        raise ProvenanceError(
            f"row {index} has no '{PROVENANCE_FIELD}'; every row must say where "
            "its runtime came from, one of "
            f"{', '.join(p.value for p in Provenance)}")
    raw = row[PROVENANCE_FIELD]
    try:
        return Provenance(raw)
    except ValueError:
        raise ProvenanceError(
            f"row {index}: unknown provenance {raw!r}; expected one of "
            f"{', '.join(p.value for p in Provenance)}") from None


def select_trainable(
    rows: Sequence[Mapping[str, object]],
) -> tuple[list[Mapping[str, object]], dict[str, int]]:
    """학습에 쓸 행과, 출처별 개수.

    거르는 것이지 실패하는 것이 아니다. calibration 기록이 섞인 파일을 주는 것은
    정상이고 -- 한 파일에 다 모아 두는 편이 낫다 -- 학습이 그것을 빼면 된다.
    """
    counts: Counter = Counter()
    kept: list[Mapping[str, object]] = []
    for index, row in enumerate(rows):
        source = provenance_of(row, index)
        counts[source.value] += 1
        if source in TRAINABLE:
            kept.append(row)
    return kept, dict(sorted(counts.items()))


def is_production_ready(counts: Mapping[str, int]) -> bool:
    """실측·실기록만으로 학습됐고, 한 줄이라도 있는가.

    Cross-GPU 검증 같은 다른 조건은 여기서 보지 않는다. 이 함수가 참이어도
    :func:`limitations_for` 가 비어 있지 않을 수 있고, 그 목록이 metadata에 함께
    실린다.
    """
    real = sum(counts.get(source.value, 0) for source in REAL)
    everything = sum(counts.get(source.value, 0)
                     for source in TRAINABLE)
    return real > 0 and real == everything


def limitations_for(
    counts: Mapping[str, int],
    measured_gpu_models: Sequence[str] = (),
) -> list[str]:
    """이 모델을 쓰는 사람이 알아야 하는 것. metadata에 그대로 실린다.

    ``measured_gpu_models`` 는 **실측 행의** GPU 이름만 받는다. 합성 행의 것을
    넣으면 안 된다 -- 부트스트랩 생성기는 GPU 다섯 종류를 만들어 내므로 "여러 GPU를
    덮었다"는 검사를 통과해 버리고, 그러면 cross-GPU 검증을 한 적이 없는데도 그
    경고가 사라진다. 실제로 처음 구현했을 때 그렇게 됐다. 합성 데이터의 다양성은
    검증의 증거가 아니다.

    빈 목록을 돌려주는 경우는 지금 없다. 그래도 조건마다 따로 붙이는 이유는,
    실제 데이터가 쌓이면 사라져야 할 문장과 남아야 할 문장이 다르기 때문이다.
    """
    notes: list[str] = []
    bootstrap = counts.get(Provenance.BOOTSTRAP.value, 0)
    if bootstrap:
        notes.append(
            f"{bootstrap} of the training rows are BOOTSTRAP synthetic data. "
            "Validation error measures how closely XGBoost reproduced the "
            "generator's formula, not how well it predicts real runtime.")
    measured = counts.get(Provenance.MEASURED.value, 0)
    if measured == 0:
        notes.append(
            "No MEASURED full-job runtime is in the training set.")
    calibration = counts.get(Provenance.CALIBRATION.value, 0)
    if calibration:
        notes.append(
            f"{calibration} CALIBRATION rows were present and excluded: they "
            "are partial runs, and the 11-feature contract cannot express "
            "where a run was cut short.")
    distinct = len(set(measured_gpu_models))
    if distinct == 0:
        notes.append(
            "Cross-GPU prediction accuracy has not been validated against any "
            "measured runtime. The GPU variety in the training set is "
            "synthetic and is not evidence of anything.")
    elif distinct == 1:
        notes.append(
            f"Measured runtime data is concentrated on a single GPU model "
            f"({next(iter(set(measured_gpu_models)))}). Cross-GPU prediction "
            "accuracy has not been validated.")
    return notes
