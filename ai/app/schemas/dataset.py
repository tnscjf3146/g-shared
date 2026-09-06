"""학습에 쓸 데이터셋이 어떻게 생겼는지, Backend가 알려주는 값들.

Scheduler는 데이터셋 파일 자체를 보지 않는다. 이 다섯 개 숫자가 데이터셋에 대해
아는 전부이고, 이후 Runtime Predictor가 "이 작업이 얼마나 걸릴지"를 추정하는 근거가 된다.

그래서 여기서 막지 못한 잘못된 값은 그대로 예측 입력이 된다. 검증이 이 경계에 있는 이유다.
"""

from typing import Annotated

from pydantic import Field, model_validator

from app.schemas.base import SchedulerSchema

#: 다섯 값 모두 0이 허용되지 않는 이유는 각 필드 설명에 적었다. 요약하면
#: 어느 하나라도 0인 데이터셋은 학습할 것이 없는 데이터셋이다.
PositiveInt = Annotated[int, Field(gt=0)]
PositiveFloat = Annotated[float, Field(gt=0)]


class DatasetMetadata(SchedulerSchema):
    """학습 데이터셋의 크기와 모양.

    상한은 두지 않는다. "이 정도면 너무 크다"는 판단은 이 계층이 아니라
    GPU 선택과 실행 가능성 검증이 할 일이고, 근거 없는 상한은 멀쩡한 요청을 막는다.
    """

    dataset_size_bytes: PositiveInt = Field(
        description=(
            "데이터셋 파일 전체 크기(byte). 전송과 디스크 사용량을 좌우한다. "
            "0 byte 파일은 학습할 내용이 없으므로 허용하지 않는다."
        ),
    )
    sample_count: PositiveInt = Field(
        description=(
            "학습 샘플 개수. 1 epoch에 몇 번 순회하는지를 정하므로 실행 시간에 직접 비례한다. "
            "샘플이 0개면 학습 자체가 성립하지 않는다."
        ),
    )
    total_tokens: PositiveInt = Field(
        description=(
            "데이터셋 전체 토큰 수. 문자 수나 byte 수가 아니라 tokenizer가 끊은 단위이며, "
            "Transformer 계열의 실제 계산량에 가장 가까운 값이다. 0이면 학습할 내용이 없다."
        ),
    )
    avg_seq_length: PositiveFloat = Field(
        description=(
            "샘플 하나의 평균 토큰 길이. 평균이므로 정수가 아닐 수 있어 실수로 받는다. "
            "패딩 후 실제 계산량이 얼마나 낭비되는지를 max_seq_length와 함께 말해 준다."
        ),
    )
    max_seq_length: PositiveInt = Field(
        description=(
            "샘플 하나의 최대 토큰 길이. 배치를 채울 때 기준이 되는 길이이자 "
            "VRAM 사용량의 상한을 좌우한다. 0이면 길이가 있는 샘플이 하나도 없다는 뜻이다."
        ),
    )

    @model_validator(mode="after")
    def _average_cannot_exceed_the_maximum(self) -> "DatasetMetadata":
        """평균이 최댓값보다 클 수는 없다.

        임의로 정한 한계가 아니라 두 필드의 정의에서 바로 따라 나오는 관계다.
        이걸 어긴 metadata는 어딘가에서 잘못 계산된 것이고, 그대로 통과시키면
        예측기가 앞뒤가 맞지 않는 값을 근거로 시간을 추정하게 된다.
        """
        if self.avg_seq_length > self.max_seq_length:
            raise ValueError(
                "avgSeqLength cannot be greater than maxSeqLength "
                f"({self.avg_seq_length} > {self.max_seq_length})"
            )
        return self
