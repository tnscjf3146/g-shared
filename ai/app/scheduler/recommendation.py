"""예측 → 순위 → 추천을 잇는 한 걸음.

**같은 프로세스 안에서 예측기를 직접 부른다.** 이미 떠 있는
``/scheduler/runtime/predict`` 를 HTTP로 다시 부르지 않는다 -- 자기 자신에게 보내는
요청은 직렬화와 왕복 비용을 내면서, 그 사이에 죽을 수 있는 지점을 하나 더 만들고,
로그를 두 배로 늘린다. 얻는 것은 없다.

다섯 책임이 각각 다른 곳에 있다.

* :mod:`app.scheduler.vram_requirement` -- 이 Job이 VRAM을 얼마나 요구하는지
* :mod:`app.scheduler.feasibility` -- 이 Job을 돌릴 수 있는 GPU인지
* :mod:`app.scheduler.runtime_predictor` -- 그 GPU에서 몇 초 걸릴지
* :mod:`app.scheduler.ranking` -- 그 숫자로 줄 세우고 하나 고르기
* 이 파일 -- 넷을 잇고, 요청을 feature 행으로 바꾸기

**요구량은 확정된 학습 설정으로 계산한다.** 이 요청의 ``trainingConfig`` 는 이미
확정된 값이다 -- AUTO라면 Backend가 ``/scheduler/training-config`` 로 받은 결과를,
CUSTOM이라면 사용자가 정한 값을 그대로 싣는다. 그래서 요구량 계산은 이 함수의 맨
앞, 걸러내기 직전에 한 번 돈다. GPU를 보고 AUTO 설정을 다시 고르지는 않는다.

**순서가 계약의 일부다.** 걸러내기가 예측보다 먼저다. 뒤바꿔도 최종 응답은 같아
보이지만, 그때는 실행되지도 않을 GPU마다 XGBoost를 부르게 되고, 후보가 늘수록 그
낭비도 같이 는다.

예측기 안에 정렬과 추천까지 넣지 않은 이유는 그것들이 모델과 함께 바뀌지 않기
때문이다. 순위 규칙은 모델을 다시 학습해도 그대로고, 모델을 바꿔도 그대로다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from app.schemas.recommendation import JobRequirements, RecommendationRequest
from app.scheduler.feasibility import filter_feasible_candidates
from app.scheduler.ranking import RankedCandidate, rank_candidates, recommend
from app.scheduler.runtime_features import RuntimeFeatures, build_runtime_features
from app.scheduler.vram_requirement import VramRequirement, estimate_required_vram_mb


class PredictsRuntime(Protocol):
    """이 서비스가 예측기에게 요구하는 전부.

    :class:`~app.scheduler.runtime_predictor.RuntimePredictor` 가 그대로 만족한다.
    Protocol로 둔 것은 테스트가 XGBoost를 띄우지 않고도 이 배선을 확인할 수 있게
    하기 위해서다 -- 여기서 검증할 것은 예측의 정확도가 아니라 연결이다.
    """

    def predict(self, rows: Sequence[RuntimeFeatures]) -> tuple[float, ...]: ...


@dataclass(frozen=True)
class Recommendation:
    """순위 전체와, 그중 1위. 그리고 걸러내기에 쓴 VRAM 요구량."""

    rankings: tuple[RankedCandidate, ...]
    recommended_worker_id: str
    vram_requirement: VramRequirement

    @property
    def required_vram_mb(self) -> int:
        return self.vram_requirement.required_vram_mb


def required_vram_for(request: RecommendationRequest) -> VramRequirement:
    """이 요청의 확정된 학습 설정과 데이터셋으로 VRAM 요구량을 정한다.

    ``requirements.minVramMb`` 는 측정 칸이 없을 때의 값이다. 측정 칸이 있으면 그 칸이
    우선한다 -- :mod:`app.scheduler.vram_requirement` 참고.
    """
    return estimate_required_vram_mb(
        model_id=request.model_id,
        batch_size=request.training_config.batch_size,
        max_length=request.training_config.max_length,
        max_seq_length=request.dataset.max_seq_length,
        legacy_min_vram_mb=request.requirements.min_vram_mb,
    )


def recommend_worker(
    request: RecommendationRequest, predictor: PredictsRuntime
) -> Recommendation:
    """돌릴 수 있는 후보를 추리고, 그것들만 예측해 줄 세우고, 하나를 고른다.

    **거르는 것이 먼저다.** 이 함수를 지나 예측기에 닿는 후보는 전부 이 Job을 돌릴
    수 있는 GPU이며, 그래서 순위에도 실행 가능한 GPU만 남는다. 탈락한 후보는 응답
    어디에도 나오지 않는다 -- Backend가 순위를 보고 고를 텐데 거기에 못 돌릴 GPU가
    섞여 있으면 그 목록은 선택지가 아니다.

    후보를 한 번에 예측기에 넘긴다. 하나씩 부르면 같은 답이 나오지만 느리고,
    무엇보다 순서가 어긋날 자리가 생긴다.

    돌릴 수 있는 GPU가 하나도 없으면
    :class:`~app.scheduler.feasibility.NoFeasibleGpuError` 가 그대로 올라간다.
    가장 큰 GPU를 골라 물러서지 않는다 -- 그건 못 돌린다는 답을 돌릴 수 있다는
    답으로 바꿔치기하는 것이다.
    """
    requirement = required_vram_for(request)
    feasible = filter_feasible_candidates(
        JobRequirements(min_vram_mb=requirement.required_vram_mb),
        request.gpu_candidates,
    )

    rows = [
        build_runtime_features(
            dataset=request.dataset,
            training=request.training_config,
            model_id=request.model_id,
            candidate=candidate,
        )
        for candidate in feasible
    ]

    seconds = predictor.predict(rows)
    # zip이 순서를 지킨다. rows도 feasible 순서로 만들었으므로 i번째 예측은
    # i번째 후보의 것이다.
    ranked = rank_candidates([
        (candidate.worker_id, predicted)
        for candidate, predicted in zip(feasible, seconds)
    ])
    return Recommendation(
        rankings=ranked,
        recommended_worker_id=recommend(ranked),
        vram_requirement=requirement,
    )
