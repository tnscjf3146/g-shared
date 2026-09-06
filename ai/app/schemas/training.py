"""학습을 어떤 설정으로 돌릴지.

AUTO와 CUSTOM은 이 값을 *만드는 방법*이 다를 뿐, 만들어진 결과는 같은 모양이다.
그래서 이 모델에는 누가 만들었는지를 나타내는 필드가 없다. mode를 여기 넣으면
이후 모든 소비자가 "AUTO면 이렇게, CUSTOM이면 저렇게"를 다시 분기하게 되고,
같은 표현을 공유한다는 성질이 그 순간 사라진다.
"""

from typing import Annotated

from pydantic import Field

from app.schemas.base import SchedulerSchema

PositiveInt = Annotated[int, Field(gt=0)]
PositiveFloat = Annotated[float, Field(gt=0)]


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
