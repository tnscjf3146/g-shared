"""Scheduler의 API 경계. 세 개의 입구가 있다.

- ``POST /scheduler/training-config`` : AUTO. 방향 하나로 설정을 만들어 준다.
- ``POST /scheduler/input``           : 확정된 설정과 데이터셋을 받는다.
  AUTO가 만든 것이든 사용자가 직접 넣은 것이든 여기로 온다.
- ``POST /scheduler/runtime/predict`` : 작업 하나와 GPU 후보들을 받아 후보마다
  실행 시간을 예측한다. 순위도 추천도 없다.
- ``POST /scheduler/recommend``       : 같은 입력에 Job의 기술 요구사항을 더해
  받아, 돌릴 수 있는 GPU만 추린 뒤 예측하고, 그 결과를 줄 세워 하나를 추천한다.

뒤의 두 개를 나눈 이유는 답의 성격이 다르기 때문이다. 예측은 "이 GPU에서 얼마나
걸리나"라는 사실이고, 추천은 "그래서 어디로 보낼까"라는 결정이다. Backend가 순위
없이 예측만 원할 때 추천까지 받아 무시해야 할 이유가 없다.

Pydantic이 이미 각 필드를 검증했으므로 핸들러에는 필드 검사가 없다.
``/scheduler/input`` 은 설정과 데이터셋을 함께 보는 정책 검사도 한 번 거치는데,
현재 그 검사에는 규칙이 하나도 없어 아무것도 막지 않는다 -- 이유는
:mod:`app.scheduler.policy` 참고.
"""

from fastapi import APIRouter, HTTPException, Request, status

from app.schemas.recommendation import (
    RankedPrediction,
    RecommendationRequest,
    RecommendationResponse,
)
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
from app.scheduler.feasibility import NoFeasibleGpuError
from app.scheduler.features import build_dataset_features
from app.scheduler.policy import PolicyReport, check_training_policy
from app.scheduler.ranking import RankingError
from app.scheduler.recommendation import recommend_worker
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


@router.post("/recommend", response_model=RecommendationResponse)
def recommend_gpu(
    request: RecommendationRequest, http_request: Request
) -> RecommendationResponse:
    """돌릴 수 있는 후보를 추리고, 예측하고, 줄 세우고, 하나를 고른다.

    ``/runtime/predict`` 를 HTTP로 다시 부르지 않는다. 같은 프로세스 안의 예측기를
    직접 쓴다 -- 자기 자신에게 보내는 요청은 비용만 늘리고 실패할 자리를 하나 더
    만든다.

    모델이 없으면 추천도 없다. benchmark가 가장 높은 GPU를 고르는 식으로 물러서지
    않는다 -- 그런 답은 예측을 근거로 한 추천이 아니면서 그렇게 보인다.

    답을 내지 못하는 세 가지를 각각 다른 상태 코드로 가른다. Backend가 셋에 대해
    해야 할 일이 다르기 때문이다.

    * **422** -- 요청이 잘못됐다. 고쳐서 다시 보내야 한다.
    * **503** -- Runtime 모델을 못 불러왔다. Scheduler 쪽 문제이고, 요청은 그대로
      두고 나중에 다시 보내면 된다.
    * **409** -- 요청도 모델도 멀쩡한데 이 후보들 중에 Job을 돌릴 수 있는 GPU가
      없다. 다시 보내도 같은 답이고, 더 큰 GPU가 붙어야 달라진다.
    """
    loaded = getattr(http_request.app.state, "runtime_model", None)
    if loaded is None or not loaded.is_available:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(loaded.error if loaded else "the runtime model was never loaded"),
        )

    try:
        chosen = recommend_worker(request, loaded.predictor)
    except NoFeasibleGpuError as error:
        # 요청이 잘못된 것도, 모델이 없는 것도 아니다. 지금 이 후보들로는 답이 없다.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=[{
                "type": "no_feasible_gpu",
                "loc": ["body", "gpuCandidates"],
                "msg": str(error),
            }],
        ) from error
    except UnknownCategoryError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[{
                "type": "unknown_category",
                "loc": ["body", error.feature],
                "msg": str(error),
            }],
        ) from error
    except (PredictionError, RankingError) as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(error),
        ) from error

    return RecommendationResponse(
        scheduler_model_version=loaded.predictor.model_version,
        recommended_worker_id=chosen.recommended_worker_id,
        rankings=[
            RankedPrediction(
                rank=entry.rank,
                worker_id=entry.worker_id,
                predicted_runtime_sec=entry.predicted_runtime_sec,
            )
            for entry in chosen.rankings
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
