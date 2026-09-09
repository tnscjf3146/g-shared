"""예측된 실행 시간으로 GPU 후보를 줄 세운다. 규칙 하나뿐이다.

**빨리 끝나는 GPU가 앞이다.** 그것이 전부다.

``benchmarkTokensPerSec``, ``gpuModel``, ``vramMb`` 로 다시 점수를 매기지 않는 이유는
그 값들이 **이미 예측에 들어가 있기** 때문이다. 예측기가 그것들을 보고 시간을
답했는데 여기서 또 가중치를 주면 같은 GPU 성능이 두 번 반영된다. 그러면 순위를
움직이는 것이 무엇인지 아무도 설명할 수 없게 된다.

가격도, 수익도, Worker 상태도 보지 않는다. 앞의 둘은 Backend의 일이고, 뒤의 것은
Backend가 후보를 추려 보낼 때 이미 판단한 것이다.

Tie를 어떻게 다루는지는 :func:`rank_candidates` 에 적었다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


class RankingError(ValueError):
    """줄을 세울 수 없다. 후보가 없거나, 예측이 예측이 아니거나."""


@dataclass(frozen=True)
class RankedCandidate:
    """순위가 정해진 후보 하나."""

    rank: int
    worker_id: str
    predicted_runtime_sec: float


def rank_candidates(
    predictions: Sequence[tuple[str, float]]
) -> tuple[RankedCandidate, ...]:
    """``(workerId, 예측 초)`` 들을 빠른 순으로. 순위는 1부터 이어진다.

    **Tie는 workerId로 가른다.** 같은 작업을 같은 사양의 GPU 둘에 물으면 예측이
    글자 그대로 같은 값으로 나오고, 그때 어느 쪽이 1위가 될지는 정렬의
    안정성에 맡길 문제가 아니다 -- 같은 요청을 두 번 보낸 Backend가 다른 답을
    받으면 안 된다. workerId는 성능을 나타내는 값이 아니라 순서를 재현 가능하게
    만드는 용도로만 쓰인다.

    **epsilon은 두지 않는다.** 1000.0000001과 1000.0000002를 "같다"고 묶는 정책도
    생각할 수 있지만, 그런 비교는 추이적이지 않다 -- a와 b가 같고 b와 c가 같은데
    a와 c는 다를 수 있고, 그러면 정렬 결과가 입력 순서에 따라 달라진다. 재현성을
    위해 도입한 규칙이 재현성을 깨뜨리는 셈이다. 정확한 값으로 비교하면 그 두
    후보의 순서도 늘 같게 나오고, 애초에 1e-7초 차이로 갈리는 순위는 어느 쪽을
    골라도 같은 선택이다.

    입력 순서는 결과에 영향을 주지 않는다.
    """
    if not predictions:
        # 줄 세울 것이 없다. 빈 목록을 돌려주면 호출한 쪽이 1위를 꺼내다 터진다.
        raise RankingError("there are no GPU candidates to rank")

    checked = [
        (worker_id, _a_real_runtime(worker_id, seconds))
        for worker_id, seconds in predictions
    ]
    ordered = sorted(checked, key=lambda entry: (entry[1], entry[0]))
    return tuple(
        RankedCandidate(rank=position, worker_id=worker_id, predicted_runtime_sec=seconds)
        for position, (worker_id, seconds) in enumerate(ordered, start=1)
    )


def recommend(ranked: Sequence[RankedCandidate]) -> str:
    """1위 Worker. 추천은 규칙 한 줄이지 별도의 모델이 아니다."""
    if not ranked:
        raise RankingError("there is nothing to recommend")
    return ranked[0].worker_id


def _a_real_runtime(worker_id: str, seconds: object) -> float:
    """실행 시간이라고 부를 수 있는 값인지.

    예측기가 이미 같은 검사를 한다. 여기서 또 하는 이유는 이 함수가 순위의
    입구이기 때문이다 -- 0이나 NaN이 들어오면 그것이 1위가 되고, 그 Worker가
    "0초에 끝난다"는 이유로 추천된다. 그런 값을 999999나 평균으로 바꿔 계속
    진행하지 않는다. 줄을 세울 수 없으면 세우지 않는다.
    """
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise RankingError(
            f"{worker_id}: predictedRuntimeSec must be a number, got {seconds!r}")
    value = float(seconds)
    if not math.isfinite(value):
        raise RankingError(
            f"{worker_id}: predictedRuntimeSec must be finite, got {value!r}")
    if value <= 0:
        raise RankingError(
            f"{worker_id}: predictedRuntimeSec must be greater than zero, got {value}")
    return value
