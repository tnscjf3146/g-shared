"""Scheduler의 API 경계. 두 개의 입구가 있다.

- ``POST /scheduler/training-config`` : AUTO. 방향 하나로 설정을 만들어 준다.
- ``POST /scheduler/input``           : 확정된 설정과 데이터셋을 받는다.
  AUTO가 만든 것이든 사용자가 직접 넣은 것이든 여기로 온다.

Pydantic이 이미 각 필드를 검증했으므로 핸들러에는 필드 검사가 없다.
``/scheduler/input`` 은 설정과 데이터셋을 함께 보는 정책 검사도 한 번 거치는데,
현재 그 검사에는 규칙이 하나도 없어 아무것도 막지 않는다 -- 이유는
:mod:`app.scheduler.policy` 참고.
"""

from fastapi import APIRouter, HTTPException, status

from app.schemas.scheduler_input import (
    AutoTrainingConfigRequest,
    SchedulerInputRequest,
    SchedulerInputResponse,
)
from app.schemas.training import TrainingConfig
from app.scheduler.auto_config import build_auto_config
from app.scheduler.features import build_dataset_features
from app.scheduler.policy import PolicyReport, check_training_policy

router = APIRouter(prefix="/scheduler", tags=["scheduler"])


@router.post("/training-config", response_model=TrainingConfig)
def create_auto_training_config(request: AutoTrainingConfigRequest) -> TrainingConfig:
    """AUTO: 학습 방향과 데이터셋으로 학습 설정을 만든다.

    돌려주는 것은 CUSTOM이 보내는 것과 같은 :class:`TrainingConfig` 다. 그대로
    ``/scheduler/input`` 에 보내도 되고, 사용자가 화면에서 고쳐서 보내도 된다 --
    그 순간부터는 CUSTOM이다.
    """
    return build_auto_config(request.mode, request.dataset)


@router.post("/input", response_model=SchedulerInputResponse)
def accept_scheduler_input(request: SchedulerInputRequest) -> SchedulerInputResponse:
    """확정된 설정과 데이터셋을 받아 Scheduler 내부 표현으로 옮긴다.

    정책 검사가 여기 있는 이유: 이 엔드포인트가 설정과 데이터셋을 **둘 다** 받는
    유일한 곳이다. 검사를 위한 별도 엔드포인트를 만들면 같은 판단이 두 곳에 살게 된다.

    현재 :func:`check_training_policy` 에는 규칙이 없어 이 분기는 실행되지 않는다.
    승인 이미지의 preprocessing / DataLoader 계약이 정해지면 규칙만 추가하면 되도록
    배선을 남겨 둔다. 응답으로 나가는 모양은 :func:`_as_error_detail` 이 정하며,
    그 함수는 규칙과 무관하게 따로 테스트된다.
    """
    report = check_training_policy(request.training, request.dataset)
    if not report.is_acceptable:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_as_error_detail(report),
        )

    features = build_dataset_features(request.dataset)
    return SchedulerInputResponse(
        dataset=request.dataset,
        training=request.training,
        features=features.as_mapping(),
    )


def _as_error_detail(report: PolicyReport) -> list[dict[str, object]]:
    """정책 위반을 FastAPI의 검증 오류와 같은 모양으로 옮긴다.

    Backend가 오류를 한 가지 방법으로만 읽으면 되도록 ``type``/``loc``/``msg`` 를
    그대로 쓴다. ``type`` 에 정책 코드가 들어가므로 스키마 오류와는 구분된다.
    """
    return [
        {
            "type": violation.code,
            "loc": ["body", "training", violation.field],
            "msg": violation.message,
        }
        for violation in report.violations
    ]
