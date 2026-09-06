"""Scheduler 입력을 받는 API 경계.

이 라우터가 하는 일은 딱 두 가지다: FastAPI가 검증한 요청을 받고, 전처리를 한 번
호출해서 결과를 돌려준다. 검증 코드가 여기 없는 것은 빠뜨려서가 아니라
Pydantic 스키마가 이미 다 했기 때문이다 -- 잘못된 요청은 이 함수에 도달하지 않는다.
"""

from fastapi import APIRouter

from app.schemas.scheduler_input import SchedulerInputRequest, SchedulerInputResponse
from app.scheduler.features import build_dataset_features

router = APIRouter(prefix="/scheduler", tags=["scheduler"])


@router.post("/input", response_model=SchedulerInputResponse)
def accept_scheduler_input(request: SchedulerInputRequest) -> SchedulerInputResponse:
    """요청을 검증하고, Scheduler 내부 표현으로 옮긴 결과를 되돌려 준다.

    아직 실행 시간을 예측하지도, GPU를 고르지도 않는다. 이 단계에서 확정하는 것은
    "무엇이 들어오는가"와 "그것이 내부에서 어떤 모양이 되는가" 두 가지다.
    """
    features = build_dataset_features(request.dataset)
    return SchedulerInputResponse(
        dataset=request.dataset,
        training=request.training,
        features=features.as_mapping(),
    )
