"""이 Job을 그 GPU가 **돌릴 수 있는가**. 얼마나 걸리는가가 아니다.

Backend와 나누는 경계가 여기에 있다. Backend는 "지금 일을 받을 수 있는 Worker"를
판단한다 -- 인증됐는지, 연결돼 있는지, 하트비트가 살아 있는지, 이미 다른 Job을 돌리고
있지는 않은지. 그건 Job이 무엇이든 같은 답이다. 반면 "12GB가 필요한 이 학습을 8GB
GPU가 돌릴 수 있는가"는 Job마다 답이 달라지고, 그 판단은 Job의 요구사항과 GPU의
사양을 함께 보는 이곳에 있다.

Backend에도 같은 비교를 두면 같은 판단이 두 곳에 살게 되고, 규칙이 정교해지는 날
(batchSize·maxLength·모델 크기를 함께 보게 되는 날) 두 답이 갈린다. 그날 어느 쪽이
맞는지는 아무도 모른다.

**예측보다 먼저 돈다.** 돌지 않을 GPU가 몇 초 걸리는지 묻는 것은 답이 필요 없는
질문에 XGBoost를 부르는 일이고, 그렇게 나온 숫자가 순위에 섞이면 Backend는 실행할
수 없는 GPU를 1위로 추천받는다.

지금 규칙은 하나다: ``vramMb >= minVramMb``. Rule Engine이나 Strategy를 미리 만들지
않는다 -- 규칙이 하나일 때 그런 뼈대는 읽는 사람에게 규칙이 여럿인 것처럼 보이게 할
뿐이고, 두 번째 규칙이 실제로 어떤 모양일지는 아직 모른다.
"""

from __future__ import annotations

from typing import Sequence

from app.schemas.recommendation import JobRequirements
from app.schemas.runtime import GpuCandidate


class NoFeasibleGpuError(ValueError):
    """후보는 있었지만 이 Job을 돌릴 수 있는 GPU가 하나도 없다.

    요청이 잘못된 것이 아니다 -- 문법도 값도 정상이고, 같은 요청을 더 큰 GPU가
    붙은 뒤에 다시 보내면 답이 나온다. 그래서 422가 아니다. 모델을 못 불러온 것도
    아니므로 503도 아니다. 지금 이 후보들로는 답이 없다는 사실 그 자체이며,
    Backend가 다르게 처리해야 하는 상황이라 다른 이름을 준다.
    """

    def __init__(self, min_vram_mb: int, considered: int) -> None:
        super().__init__(
            f"none of the {considered} GPU candidates has at least "
            f"{min_vram_mb} MiB of VRAM, which this job requires"
        )
        self.min_vram_mb = min_vram_mb
        self.considered = considered


def filter_feasible_candidates(
    requirements: JobRequirements, candidates: Sequence[GpuCandidate]
) -> tuple[GpuCandidate, ...]:
    """이 Job을 돌릴 수 있는 후보만. 순서는 받은 순서 그대로다.

    순서를 그대로 두는 이유는 이 함수가 순위를 정하지 않기 때문이다. 어느 GPU가 더
    빠른지는 예측이 답할 일이고, 여기서 정렬해 두면 다음 단계가 이미 정해진 줄을
    물려받는 것처럼 보인다. 실행 가능 여부와 순위는 다른 질문이다.

    비교는 ``>=`` 다. 요구량과 정확히 같은 VRAM은 모자란 것이 아니다.

    하나도 남지 않으면 빈 튜플을 돌려주지 않고 :class:`NoFeasibleGpuError` 를 던진다.
    빈 목록을 받은 쪽은 예측기에 빈 행렬을 넣거나 순위에서 1위를 꺼내다 터지고, 그때
    나오는 오류는 "후보가 없다"가 아니라 그 다음 단계의 이름을 달고 있어 원인을
    가린다. :func:`~app.scheduler.ranking.rank_candidates` 도 같은 이유로 빈 입력을
    거절한다.
    """
    feasible = tuple(
        candidate
        for candidate in candidates
        if candidate.vram_mb >= requirements.min_vram_mb
    )
    if not feasible:
        raise NoFeasibleGpuError(requirements.min_vram_mb, len(candidates))
    return feasible
