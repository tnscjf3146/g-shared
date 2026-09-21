"""GPU 추천 요청과 응답.

Runtime 예측과 **같은 작업 입력**을 받는다. 그래서 요청 스키마를 새로 쓰지 않고
:class:`~app.schemas.runtime.RuntimePredictionRequest` 를 물려받는다 -- 거의 같은
스키마를 두 벌 두면 언젠가 한쪽에만 필드가 추가되고, 그때 두 API가 다른 계약을
갖게 된다.

물려받으면서 두 가지를 더한다.

* **같은 workerId가 두 번 들어오는 것을 거절한다.**
* **Job의 기술 요구사항(**``requirements``**)을 받는다.**

둘 다 부모가 아니라 여기에 둔 이유는 같다 -- ``/scheduler/runtime/predict`` 의 계약을
건드리지 않기 위해서다. 예측만 하는 쪽에서는 같은 Worker를 두 번 묻는 것이 쓸데없을
뿐 틀린 답을 만들지 않고, 요구사항도 필요 없다. "이 GPU에서 몇 초 걸리나"는 그 GPU가
Job을 돌릴 수 있든 없든 답이 정해지는 질문이기 때문이다. 반대로 순위를 매기는 쪽에서는
한 Worker가 두 순위를 차지하면 추천이 말이 되지 않고, 돌릴 수 없는 GPU가 1위가 되면
Backend는 실행할 수 없는 곳으로 Job을 보낸다.
"""

from typing import Annotated

from pydantic import Field, model_validator

from app.schemas.base import SchedulerSchema
from app.schemas.runtime import RuntimePredictionRequest

PositiveInt = Annotated[int, Field(gt=0)]
PositiveFloat = Annotated[float, Field(gt=0, allow_inf_nan=False)]


class JobRequirements(SchedulerSchema):
    """이 Job을 GPU가 **기술적으로** 돌릴 수 있는지 판단하는 데 필요한 값들.

    Backend가 실어 보내야 하는 이유는 Scheduler가 DB를 보지 않기 때문이다.
    ``modelId`` 나 ``dockerImageId`` 만 받아 ``base_models``/``workloads`` 를 조회하면
    Scheduler가 Backend 스키마에 묶여 그쪽 마이그레이션마다 같이 깨진다. 그렇다고
    Scheduler 안에 "1번 모델은 8GB" 같은 표를 두면 카탈로그가 두 곳에 살게 되고,
    모델이 하나 늘어난 날 한쪽만 고쳐진다. 카탈로그를 가진 쪽이 읽어서 보낸다.

    **예외가 하나 있다.** 실측한 모델·batch·길이 조합은 Scheduler가 요구량을 직접
    정한다(:mod:`app.scheduler.vram_requirement`). 요구량이 모델 하나에 상수 하나로
    정해지지 않고 학습 설정과 데이터에 따라 달라지기 때문이다. 그런 칸이 있으면
    ``minVramMb`` 보다 그 칸이 우선하고, 없을 때만 ``minVramMb`` 가 요구량이다.
    지금 Backend가 보내는 값(8192)은 모델 하한이 아니라 일괄 값이라, 둘 중 큰 쪽을
    쓰면 실측이 의미를 잃는다. 실제로 쓴 값은 응답의 ``requiredVramMb`` 에 있다.
    """

    min_vram_mb: PositiveInt = Field(
        description=(
            "Minimum GPU VRAM required to execute this job, in MiB. "
            "gpuCandidates[].vramMb와 같은 단위다. 이보다 작은 GPU는 예측 전에 "
            "걸러진다 -- 실행되지 않을 GPU의 실행 시간은 물어볼 이유가 없다."
        ),
    )


class RecommendationRequest(RuntimePredictionRequest):
    """Backend → AI Scheduler. 작업 하나와, 그것을 돌릴 수 있는 GPU들."""

    requirements: JobRequirements = Field(
        description=(
            "이 Job을 돌리는 데 필요한 기술 조건. Backend는 '지금 일을 받을 수 있는' "
            "Worker만 gpuCandidates로 보내고(운영 상태), '이 Job을 돌릴 수 있는가'는 "
            "이 값으로 Scheduler가 판단한다. Backend에서 vramMb를 미리 비교하지 "
            "않는다 -- 같은 판단이 두 곳에 살면 언젠가 두 답이 갈린다."
        ),
    )

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
    required_vram_mb: PositiveInt = Field(
        description=(
            "후보를 걸러낸 VRAM 요구량(MiB). gpuCandidates[].vramMb와 같은 단위다. "
            "실측한 모델·batchSize·min(maxLength, maxSeqLength) 조합이면 그 측정에서 "
            "정한 값이고, 아니면 요청의 requirements.minVramMb 그대로다. 화면의 "
            "'VRAM 부족' 표시도 이 값으로 해야 실제 판정과 갈리지 않는다."
        ),
    )
    rankings: list[RankedPrediction] = Field(
        min_length=1,
        description="predictedRuntimeSec 오름차순. 요청의 후보 전부가 한 번씩 들어간다.",
    )
