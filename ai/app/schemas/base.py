"""Backend와 AI Scheduler 사이 데이터 계약의 공통 규칙.

Backend(Spring)는 camelCase JSON을 보내고 Python은 snake_case로 쓴다.
그 변환을 모델마다 손으로 적으면 언젠가 한 곳이 어긋나므로 여기서 한 번만 정한다.
"""

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class SchedulerSchema(BaseModel):
    """Scheduler가 주고받는 모든 모델의 공통 설정.

    - ``alias_generator`` : 파이썬 ``dataset_size_bytes`` ↔ JSON ``datasetSizeBytes``
    - ``populate_by_name`` : 파이썬 코드에서는 snake_case 이름으로도 만들 수 있다.
      테스트와 내부 호출이 JSON 이름을 흉내 내지 않아도 된다.
    - ``extra="forbid"`` : 모르는 필드는 무시하지 않고 거절한다. 조용히 무시하면
      Backend가 오타 난 필드를 계속 보내면서 값이 반영됐다고 믿게 된다.
      계약을 지금 정의하는 단계이므로 느슨함보다 어긋남을 빨리 아는 쪽이 낫다.
    - ``frozen=True`` : 검증을 통과한 요청은 그 뒤로 바뀌지 않는다. 전처리가
      원본을 건드리는 사고를 타입 수준에서 막는다.
    - ``strict=True`` : ``"1000"`` 같은 숫자 문자열을 숫자로 바꿔 주지 않는다.
      JSON에는 숫자 타입이 있으므로 문자열로 왔다면 보내는 쪽이 잘못 만든 것이고,
      조용히 고쳐 주면 그 잘못이 계속 남는다. int를 float 자리에 넣는 것은
      strict에서도 허용되므로 ``avgSeqLength: 256`` 은 그대로 통과한다.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
        strict=True,
    )
