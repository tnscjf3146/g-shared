"""학습을 어떤 설정으로 돌릴지.

AUTO와 CUSTOM은 이 값을 *만드는 방법*이 다를 뿐, 만들어진 결과는 같은 모양이다.
그래서 이 모델에는 누가 만들었는지를 나타내는 필드가 없다. mode를 여기 넣으면
이후 모든 소비자가 "AUTO면 이렇게, CUSTOM이면 저렇게"를 다시 분기하게 되고,
같은 표현을 공유한다는 성질이 그 순간 사라진다.
"""

from enum import Enum
from typing import Annotated

from pydantic import Field

from app.schemas.base import SchedulerSchema

PositiveInt = Annotated[int, Field(gt=0)]
PositiveFloat = Annotated[float, Field(gt=0)]


class TrainingMode(str, Enum):
    """사용자가 AUTO에서 고르는 학습 방향.

    Hyperparameter 네 개를 직접 정하는 대신 "무엇을 우선할지"만 고른다.
    세 값은 같은 축의 세 지점이다 -- 왼쪽 끝이 빨리 끝나는 것, 오른쪽 끝이 잘 배우는 것.

    :class:`TrainingConfig`에는 이 값이 들어가지 않는다. Mode는 설정을 *만들 때*
    쓰는 요청의 의미이고, 만들어진 설정은 AUTO든 CUSTOM이든 같은 모양이어야 한다.

    ``str`` 을 상속하는 이유는 JSON에 enum 타입이 없어서다. 이렇게 두면 그대로
    ``"SPEED"`` 로 직렬화되고, 받을 때도 문자열 하나만 오면 된다.
    """

    #: 결과를 빨리 보는 것이 먼저. 1 epoch, 짧게 자른 시퀀스.
    SPEED = "SPEED"

    #: 특별한 이유가 없을 때의 선택.
    BALANCED = "BALANCED"

    #: 잘 배우는 것이 먼저. 오래 걸려도 데이터를 더 많이, 더 여러 번 본다.
    TRAINING = "TRAINING"


class TrainingConfig(SchedulerSchema):
    """한 번의 학습을 규정하는 네 개의 값.

    상한은 두지 않는다. 이 설정이 실제로 이 GPU에서 돌아가는지(VRAM이 충분한지 등)는
    실행 가능성 검증이 판단할 일이고, 여기서 근거 없는 상한을 두면 큰 GPU를 가진
    공급자에게만 가능한 요청까지 미리 막게 된다.
    """

    epochs: PositiveInt = Field(
        description=(
            "데이터셋 전체를 몇 번 반복 학습하는지. 실행 시간에 거의 정비례한다. "
            "0회는 학습을 하지 않겠다는 뜻이므로 허용하지 않는다."
        ),
    )
    batch_size: PositiveInt = Field(
        description=(
            "한 번의 순전파/역전파에 넣는 샘플 수. 클수록 GPU를 채우지만 VRAM을 더 쓴다. "
            "epoch당 step 수 = sampleCount / batchSize 이므로 실행 시간을 좌우한다."
        ),
    )
    learning_rate: PositiveFloat = Field(
        description=(
            "한 step에서 가중치를 얼마나 움직일지. 보통 1e-5~1e-3 규모의 작은 실수다. "
            "0이면 가중치가 전혀 변하지 않아 학습이 아니다. 음수는 손실을 키우는 방향이다."
        ),
    )
    max_length: PositiveInt = Field(
        description=(
            "모델에 넣을 토큰 길이 상한. 이보다 긴 샘플은 잘리고 짧은 샘플은 패딩된다. "
            "데이터셋의 maxSeqLength가 '데이터가 실제로 얼마나 긴가'라면 "
            "이 값은 '그중 어디까지 쓸 것인가'다. 둘은 다른 값이다."
        ),
    )
