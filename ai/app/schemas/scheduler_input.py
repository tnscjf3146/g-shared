"""Backend가 Scheduler에 보내는 한 건의 입력, 그리고 Scheduler가 이해한 결과.

두 스키마를 따로 두지 않고 하나의 요청으로 묶은 이유: 데이터셋과 학습 설정은
따로 판단할 수 없다. 같은 데이터셋도 batchSize가 다르면 실행 시간이 달라지고,
같은 설정도 데이터셋이 다르면 마찬가지다. 예측은 둘을 함께 보고 한다.
"""

from pydantic import Field

from app.schemas.base import SchedulerSchema
from app.schemas.dataset import DatasetMetadata
from app.schemas.training import TrainingConfig, TrainingMode


class SchedulerInputRequest(SchedulerSchema):
    """Backend → AI Scheduler."""

    dataset: DatasetMetadata = Field(description="학습에 쓸 데이터셋의 크기와 모양")
    training: TrainingConfig = Field(description="이 학습을 어떤 설정으로 돌릴지")


class SchedulerInputResponse(SchedulerSchema):
    """AI Scheduler → Backend. "내가 이렇게 이해했다"는 답.

    받은 값을 그대로 되돌려 주는 것은 확인용이다. Backend가 보낸 JSON과 다르면
    (예: 모르는 필드를 보냈거나 타입이 달랐다면) 여기까지 오지 못하고 422가 난다.
    """

    dataset: DatasetMetadata = Field(description="검증을 통과한 데이터셋 metadata")
    training: TrainingConfig = Field(description="검증을 통과한 학습 설정")
    features: dict[str, float] = Field(
        description=(
            "이 metadata에서 Scheduler가 만들어 낸 내부 feature. "
            "이후 Runtime Predictor가 받는 값과 같은 것이며, "
            "키 이름은 API 계약(camelCase)이 아니라 내부 이름(snake_case)이다."
        ),
    )


class AutoTrainingConfigRequest(SchedulerSchema):
    """Backend → AI Scheduler. "이 데이터셋을 이 방향으로 학습할 설정을 만들어 달라".

    사용자는 Hyperparameter 네 개 대신 방향 하나만 고른다. 답으로 돌아오는 것은
    :class:`~app.schemas.training.TrainingConfig` 그 자체이며, 어느 Mode로
    만들었는지는 담기지 않는다 -- 만들어진 뒤로는 CUSTOM이 보낸 설정과 구분할
    이유가 없기 때문이다.
    """

    dataset: DatasetMetadata = Field(description="학습에 쓸 데이터셋의 크기와 모양")
    mode: TrainingMode = Field(
        # 다른 필드와 달리 strict를 끈다. JSON에는 enum 타입이 없어 "SPEED" 라는
        # 문자열로 올 수밖에 없다. 값이 틀리면(예: "FAST") 그대로 거절된다.
        strict=False,
        description="SPEED / BALANCED / TRAINING 중 하나",
    )
