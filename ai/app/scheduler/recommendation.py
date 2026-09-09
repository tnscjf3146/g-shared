"""예측 → 순위 → 추천을 잇는 한 걸음.

**같은 프로세스 안에서 예측기를 직접 부른다.** 이미 떠 있는
``/scheduler/runtime/predict`` 를 HTTP로 다시 부르지 않는다 -- 자기 자신에게 보내는
요청은 직렬화와 왕복 비용을 내면서, 그 사이에 죽을 수 있는 지점을 하나 더 만들고,
로그를 두 배로 늘린다. 얻는 것은 없다.

세 책임이 각각 다른 곳에 있다.

* :mod:`app.scheduler.runtime_predictor` -- 몇 초 걸릴지
* :mod:`app.scheduler.ranking` -- 그 숫자로 줄 세우고 하나 고르기
* 이 파일 -- 둘을 잇고, 요청을 feature 행으로 바꾸기

예측기 안에 정렬과 추천까지 넣지 않은 이유는 그것들이 모델과 함께 바뀌지 않기
때문이다. 순위 규칙은 모델을 다시 학습해도 그대로고, 모델을 바꿔도 그대로다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from app.schemas.recommendation import RecommendationRequest
from app.scheduler.ranking import RankedCandidate, rank_candidates, recommend
from app.scheduler.runtime_features import RuntimeFeatures, build_runtime_features


class PredictsRuntime(Protocol):
    """이 서비스가 예측기에게 요구하는 전부.

    :class:`~app.scheduler.runtime_predictor.RuntimePredictor` 가 그대로 만족한다.
    Protocol로 둔 것은 테스트가 XGBoost를 띄우지 않고도 이 배선을 확인할 수 있게
    하기 위해서다 -- 여기서 검증할 것은 예측의 정확도가 아니라 연결이다.
    """

    def predict(self, rows: Sequence[RuntimeFeatures]) -> tuple[float, ...]: ...


@dataclass(frozen=True)
class Recommendation:
    """순위 전체와, 그중 1위."""

    rankings: tuple[RankedCandidate, ...]
    recommended_worker_id: str


def recommend_worker(
    request: RecommendationRequest, predictor: PredictsRuntime
) -> Recommendation:
    """작업 하나와 후보들로 순위를 만들고 하나를 고른다.

    후보를 한 번에 예측기에 넘긴다. 하나씩 부르면 같은 답이 나오지만 느리고,
    무엇보다 순서가 어긋날 자리가 생긴다.
    """
    rows = [
        build_runtime_features(
            dataset=request.dataset,
            training=request.training_config,
            model_id=request.model_id,
            candidate=candidate,
        )
        for candidate in request.gpu_candidates
    ]

    seconds = predictor.predict(rows)
    # zip이 순서를 지킨다. rows도 candidates 순서로 만들었으므로 i번째 예측은
    # i번째 후보의 것이다.
    ranked = rank_candidates([
        (candidate.worker_id, predicted)
        for candidate, predicted in zip(request.gpu_candidates, seconds)
    ])
    return Recommendation(rankings=ranked, recommended_worker_id=recommend(ranked))
