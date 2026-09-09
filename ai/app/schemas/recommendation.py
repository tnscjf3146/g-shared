"""GPU 추천 요청과 응답.

Runtime 예측과 **같은 작업 입력**을 받는다. 그래서 요청 스키마를 새로 쓰지 않고
:class:`~app.schemas.runtime.RuntimePredictionRequest` 를 물려받는다 -- 거의 같은
스키마를 두 벌 두면 언젠가 한쪽에만 필드가 추가되고, 그때 두 API가 다른 계약을
갖게 된다.

물려받으면서 딱 하나를 더한다: **같은 workerId가 두 번 들어오는 것을 거절한다.**
이 규칙을 부모가 아니라 여기에 둔 이유는 ``/scheduler/runtime/predict`` 의 계약을
건드리지 않기 위해서다. 예측만 하는 쪽에서는 같은 Worker를 두 번 물어보는 것이
쓸데없을 뿐 틀린 답을 만들지 않지만, 순위를 매기는 쪽에서는 한 Worker가 두 순위를
차지하게 되어 추천 자체가 말이 되지 않는다.
"""

from typing import Annotated

from pydantic import Field, model_validator

from app.schemas.base import SchedulerSchema
from app.schemas.runtime import RuntimePredictionRequest

PositiveInt = Annotated[int, Field(gt=0)]
PositiveFloat = Annotated[float, Field(gt=0, allow_inf_nan=False)]


class RecommendationRequest(RuntimePredictionRequest):
    """Backend → AI Scheduler. 작업 하나와, 그것을 돌릴 수 있는 GPU들."""

    @model_validator(mode="after")
    def _each_worker_appears_once(self) -> "RecommendationRequest":
        """같은 Worker가 두 번 후보로 오면 거절한다.

        서로 다른 GPU 후보인 것처럼 다루면 한 기계가 1위와 3위를 동시에 차지하고,
        Backend는 그 목록을 실행 가능한 서로 다른 선택지로 읽는다. 조용히 하나를
        지우는 것도 답이 아니다 -- 보낸 쪽은 자기가 무엇을 잘못 보냈는지 모른다.
        """
        seen: set[str] = set()
        for candidate in self.gpu_candidates:
            if candidate.worker_id in seen:
                raise ValueError(
                    f"workerId {candidate.worker_id!r} appears more than once in "
                    "gpuCandidates; each Worker may be offered once"
                )
            seen.add(candidate.worker_id)
        return self


class RankedPrediction(SchedulerSchema):
    """순위가 매겨진 후보 하나."""

    rank: PositiveInt = Field(
        description="1부터 시작하는 순위. 1이 가장 빨리 끝날 것으로 예측된 GPU다.",
    )
    worker_id: str = Field(description="요청에 들어 있던 그 GPU")
    predicted_runtime_sec: PositiveFloat = Field(
        description=(
            "Runtime 예측기가 답한 초. 반올림하거나 분·시간으로 바꾸지 않고 "
            "그대로 싣는다 -- 표시 형식은 화면이 정할 일이고, 여기서 미리 "
            "정밀도를 버리면 Backend가 되돌릴 방법이 없다."
        ),
    )


class RecommendationResponse(SchedulerSchema):
    """AI Scheduler → Backend. 순위와, 그중 하나."""

    scheduler_model_version: str = Field(
        description=(
            "예측을 만든 Runtime 모델의 버전. 순위 매기기는 별도의 ML 모델이 "
            "아니므로 랭킹용 버전을 따로 만들지 않는다 -- 이 답에서 학습된 것은 "
            "예측뿐이고, 나머지는 규칙이다."
        ),
    )
    recommended_worker_id: str = Field(
        description=(
            "1위 Worker. 후보가 하나뿐이어도 그 하나가 추천이다. 추천할 것이 "
            "없는 응답은 만들어지지 않는다 -- 그런 요청은 애초에 거절된다."
        ),
    )
    rankings: list[RankedPrediction] = Field(
        min_length=1,
        description="predictedRuntimeSec 오름차순. 요청의 후보 전부가 한 번씩 들어간다.",
    )
