"""Scheduler의 API 경계. 세 개의 입구가 있다.

- ``POST /scheduler/training-config`` : AUTO. 방향 하나로 설정을 만들어 준다.
- ``POST /scheduler/input``           : 확정된 설정과 데이터셋을 받는다.
  AUTO가 만든 것이든 사용자가 직접 넣은 것이든 여기로 온다.
- ``POST /scheduler/runtime/predict`` : 작업 하나와 GPU 후보들을 받아 후보마다
  실행 시간을 예측한다. 순위도 추천도 없다.

Pydantic이 이미 각 필드를 검증했으므로 핸들러에는 필드 검사가 없다.
``/scheduler/input`` 은 설정과 데이터셋을 함께 보는 정책 검사도 한 번 거치는데,
현재 그 검사에는 규칙이 하나도 없어 아무것도 막지 않는다 -- 이유는
:mod:`app.scheduler.policy` 참고.
"""

from fastapi import APIRouter, HTTPException, Request, status

from app.schemas.runtime import (
    RuntimePrediction,
    RuntimePredictionRequest,
    RuntimePredictionResponse,
)
from app.schemas.scheduler_input import (
    AutoTrainingConfigRequest,
    SchedulerInputRequest,
    SchedulerInputResponse,
)
from app.schemas.training import TrainingConfig
from app.scheduler.auto_config import build_auto_config
from app.scheduler.features import build_dataset_features
from app.scheduler.policy import PolicyReport, check_training_policy
from app.scheduler.runtime_features import UnknownCategoryError, build_runtime_features
from app.scheduler.runtime_predictor import PredictionError

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


@router.post("/runtime/predict", response_model=RuntimePredictionResponse)
def predict_runtime(
    request: RuntimePredictionRequest, http_request: Request
) -> RuntimePredictionResponse:
    """GPU 후보마다 이 작업이 몇 초 걸릴지.

    후보를 한 번에 받는 이유는 Backend가 후보 하나마다 HTTP 왕복을 하지 않게 하기
    위해서다. 예측도 한 번에 한 행렬로 하며, 응답은 요청과 같은 순서로 나간다.

    돌려주지 않는 것: 순위, 추천 GPU, 비용. 순위는 다음 작업(gpu-ranking)의 책임이고
    비용은 Backend가 계산한다. 여기서 그것까지 내보내면 두 곳이 같은 판단을 하게 된다.
    """
    loaded = getattr(http_request.app.state, "runtime_model", None)
    if loaded is None or not loaded.is_available:
        # 모델 없이 그럴듯한 숫자를 만들어 내지 않는다. 못 한다고 말한다.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(loaded.error if loaded else "the runtime model was never loaded"),
        )

    rows = [
        build_runtime_features(
            dataset=request.dataset,
            training=request.training_config,
            model_id=request.model_id,
            candidate=candidate,
        )
        for candidate in request.gpu_candidates
    ]

    try:
        seconds = loaded.predictor.predict(rows)
    except UnknownCategoryError as error:
        # 본 적 없는 GPU나 기반 모델. 임의의 코드를 넣어 답을 만들지 않는다.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[{
                "type": "unknown_category",
                "loc": ["body", error.feature],
                "msg": str(error),
            }],
        ) from error
    except PredictionError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(error),
        ) from error

    return RuntimePredictionResponse(
        scheduler_model_version=loaded.predictor.model_version,
        predictions=[
            RuntimePrediction(
                worker_id=candidate.worker_id,
                predicted_runtime_sec=predicted,
            )
            # zip이 순서를 보장한다. rows도 candidates 순서로 만들었으므로
            # 예측 i번째가 후보 i번째다.
            for candidate, predicted in zip(request.gpu_candidates, seconds)
        ],
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
