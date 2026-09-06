"""CUSTOM: 사용자가 직접 넣은 설정이 이 데이터셋에 대해 말이 되는지 본다.

**고치지 않는다. 왜 안 되는지만 돌려준다.**

여기 있는 규칙과 :class:`~app.schemas.training.TrainingConfig` 의 Pydantic 검증은
보는 것이 다르다.

- Pydantic : 필드 하나만 보면 판단할 수 있는 것 (``epochs > 0``)
- 이 파일   : **데이터셋과 함께 봐야** 판단할 수 있는 것

그래서 이 검증이 Pydantic 안에 들어갈 수 없다. ``TrainingConfig`` 는 데이터셋을
모르기 때문이다.

현재 규칙은 **하나도 없다.**
----------------------------

설정과 데이터셋만 보고 "이건 확실히 실패한다"고 말할 수 있는 조건이 지금은 없다.
다음 두 가지를 후보로 검토했다가 뺐다.

- ``maxLength > maxSeqLength`` : 데이터에 그만큼 긴 샘플이 없다는 뜻이지만,
  ``maxLength`` 가 truncation 상한이고 dynamic padding을 쓴다면 아무 문제 없이
  돌아간다. 실제로 낭비인지 아닌지는 승인 이미지가 어떻게 padding 하느냐에 달렸다.
- ``batchSize > sampleCount`` : 일반적인 DataLoader는 남은 샘플로 더 작은 배치를
  하나 만들어 그대로 돌린다. ``drop_last=True`` 라면 step이 0이 되어 실패하겠지만,
  그 설정이 무엇인지 정해진 바가 없다.

두 조건 모두 **승인 Docker Image의 preprocessing / DataLoader 계약**이 확정돼야
"실패가 보장된다"고 말할 수 있다. 그 전에 거절하면, 멀쩡히 돌아갈 학습을 근거 없이
막는 셈이 된다. 사용자 값을 함부로 바꾸지 않는다는 원칙은 함부로 거절하지 않는다는
원칙과 같은 것이다.

계약이 정해지면 :func:`check_training_policy` 안에 규칙을 추가하면 된다.
그때 필요한 배선(응답 모양까지)은 이미 전부 놓여 있다.

여기서 하지 않는 것: **이 GPU에서 실제로 돌아가는가.** VRAM이 batch_size와
max_length를 감당하는지는 GPU 후보를 알아야 답할 수 있고, 그것은 GPU 실행 가능성
검증의 책임이다. 같은 판단을 두 계층에서 하면 언젠가 두 답이 갈린다.
"""

from dataclasses import dataclass

from app.schemas.dataset import DatasetMetadata
from app.schemas.training import TrainingConfig


@dataclass(frozen=True)
class PolicyViolation:
    """거절 사유 하나. 어떤 필드가, 무엇과 비교해서, 왜 안 되는지."""

    #: 사람이 아니라 코드가 분기할 이름. 메시지 문구가 바뀌어도 이건 안 바뀐다.
    code: str
    #: 문제가 된 Training Config의 필드 이름(계약 이름, camelCase).
    field: str
    #: 사람이 읽을 설명. 비교한 두 값을 모두 담아 무엇과 견줬는지 드러낸다.
    message: str


@dataclass(frozen=True)
class PolicyReport:
    """검사 결과 전체.

    처음 발견한 하나가 아니라 위반을 모두 담는다. 하나 고쳐 다시 보내고
    또 하나를 듣는 왕복은 Pydantic도 하지 않는다.
    """

    violations: tuple[PolicyViolation, ...]

    @property
    def is_acceptable(self) -> bool:
        return not self.violations


def check_training_policy(
    config: TrainingConfig, dataset: DatasetMetadata
) -> PolicyReport:
    """설정과 데이터셋을 함께 보고, 어긋난 것을 모두 모아 돌려준다.

    두 인자 모두 읽기만 한다. 돌려주는 것은 보고서이고, 고쳐진 설정이 아니다.

    지금은 규칙이 없어 항상 빈 보고서를 돌려준다. 이유는 모듈 docstring에 있다.
    Schema가 통과시킨 설정은 이 단계에서 더 막히지 않는다.
    """
    violations: list[PolicyViolation] = []

    return PolicyReport(tuple(violations))
