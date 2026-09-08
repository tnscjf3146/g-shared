"""AUTO: 학습 방향 하나만 고르면 네 개의 Hyperparameter가 정해진다.

**이 파일이 AUTO 규칙의 전부다.** 규칙이 여기저기 흩어지면 "SPEED가 왜 이 값인가"를
아무도 대답할 수 없게 되므로, 아래 :data:`MODE_RULES` 표 하나만 읽으면 끝나도록 한다.

규칙의 모양은 두 부분이다.

1. **Mode마다 고정된 값** -- 표에 그대로 적혀 있다. 데이터셋에 따라 계산하지 않는다.
   계산식을 만들려면 "샘플이 몇 개일 때 epoch을 몇으로 할지"를 뒷받침할 측정치가
   있어야 하는데, 아직 그런 데이터가 없다. 없는 근거로 만든 식은 표보다 나쁘다.

2. **데이터셋이 가진 것을 넘지 않도록 깎기** -- ``min()`` 두 번.
   AUTO는 사용자가 아무것도 정하지 않았을 때 대신 정해 주는 자리이므로,
   데이터가 뒷받침하지 않는 값을 스스로 고르지 않는다. 넘는 값이 반드시
   실행 불가능하다는 뜻은 아니다 -- CUSTOM으로 직접 넣는 것은 막지 않는다.

Mode 사이의 관계는 하나의 축이다: 짧게 여러 개(SPEED) ↔ 길게 조금씩(TRAINING).
:data:`TOKENS_PER_STEP` 이 그 축을 고정한다.

이 파일은 **실행 가능성을 판단하지 않는다.** 여기서 정한 설정이 실제 GPU에서
돌아가는지는 GPU 검증 단계에서만 답할 수 있다.
"""

from dataclasses import dataclass

from app.schemas.dataset import DatasetMetadata
from app.schemas.training import TrainingConfig, TrainingMode

#: 한 step에 넣는 토큰 수(batch_size × max_length)를 세 Mode에서 같은 값으로 둔다.
#:
#: 초기 정책에서 batch size와 sequence length의 trade-off를 단순화하기 위한
#: 기준값이다. 세 Mode를 "짧게 여러 개 ↔ 길게 조금씩"이라는 하나의 축 위에 놓아,
#: 표에 적힌 여섯 개 숫자를 따로따로 정하지 않고 한 번에 설명할 수 있게 한다.
#:
#: **이 값이 같다고 해서 VRAM 사용량이 같아지지는 않는다.** Transformer 학습의
#: 메모리는 모델 크기, activation, attention 구현(예: FlashAttention 여부),
#: optimizer state 등에도 좌우되며, 이 곱은 그중 한 가지 요인일 뿐이다.
#: 세 Mode가 같은 GPU에서 모두 돌아간다는 뜻도 아니다.
#:
#: 실제 GPU 실행 가능성은 이후 별도의 GPU 검증 단계에서 판단한다.
TOKENS_PER_STEP = 8192


@dataclass(frozen=True)
class ModeRule:
    """Mode 하나가 정하는 값들."""

    epochs: int
    batch_size: int
    #: 시퀀스 길이의 상한. 데이터셋이 이보다 짧으면 데이터셋 쪽이 이긴다.
    max_length_cap: int
    learning_rate: float


#: AUTO 규칙 전체.
#:
#: - ``epochs`` : 실행 시간에 거의 정비례하는 유일한 값이라 Mode를 가장 잘 나타낸다.
#:   3이 SFT에서 널리 쓰이는 기본값이고, 1과 5가 그 양옆을 잡는다. 1보다 적을 수 없고,
#:   작은 SFT 데이터셋에서 5를 넘기면 과적합이 먼저 온다.
#: - ``batch_size`` × ``max_length_cap`` = :data:`TOKENS_PER_STEP`. 위 상수 설명 참고 --
#:   메모리 사용량을 맞춘 것이 아니라 표를 한 축으로 설명하기 위한 기준값이다.
#: - ``max_length_cap`` : 512 / 1024 / 2048. SFT에서 관례적으로 쓰는 길이이고
#:   2의 거듭제곱이라 패딩 버킷과 커널이 다루기 좋다. Attention 비용은 길이의
#:   제곱에 가까우므로 이 값이 Mode의 속도 차이를 만든다.
#: - ``learning_rate`` : 세 Mode가 같은 값을 쓴다. 이유는 아래.
#:
#: learning_rate가 Mode마다 다르지 않은 이유: 네 값 중 실행 시간에 영향이 없는
#: 유일한 값이고, Mode는 "얼마나 오래 걸리는가 ↔ 얼마나 잘 배우는가"의 선택이다.
#: 근거 없이 Mode마다 다른 숫자를 넣으면 표만 복잡해지고 설명할 수 없는 값이 는다.
#: 2e-4는 QLoRA/LoRA SFT에서 사실상의 기본값이다.
MODE_RULES: dict[TrainingMode, ModeRule] = {
    TrainingMode.SPEED: ModeRule(
        epochs=1, batch_size=16, max_length_cap=512, learning_rate=2e-4
    ),
    TrainingMode.BALANCED: ModeRule(
        epochs=3, batch_size=8, max_length_cap=1024, learning_rate=2e-4
    ),
    TrainingMode.TRAINING: ModeRule(
        epochs=5, batch_size=4, max_length_cap=2048, learning_rate=2e-4
    ),
}


def build_auto_config(
    mode: TrainingMode, dataset: DatasetMetadata
) -> TrainingConfig:
    """Mode와 데이터셋으로 :class:`TrainingConfig` 를 만든다. 원본은 읽기만 한다.

    데이터셋에서 쓰는 값은 딱 두 개이며, 둘 다 같은 이유로 쓴다 --
    **데이터셋이 가진 것보다 더 요구하지 않는다.**

    - ``max_seq_length`` → ``max_length = min(cap, max_seq_length)``
      데이터에 그만큼 긴 샘플이 없는데 길이를 더 잡을 이유가 없다. 실제로 얼마나
      낭비인지는 승인 이미지의 padding 방식에 달려 있으므로 단정하지 않는다.
      다만 AUTO가 스스로 고를 값은 아니다.
    - ``sample_count`` → ``batch_size = min(rule.batch_size, sample_count)``
      샘플 수보다 큰 배치를 AUTO가 먼저 고르지는 않는다. 일반적인 DataLoader라면
      더 작은 배치 하나로 돌아가겠지만, 그것은 AUTO가 의도한 설정이 아니다.

    나머지 세 값(``dataset_size_bytes``, ``total_tokens``, ``avg_seq_length``)은
    AUTO가 쓰지 않는다. 실행 시간을 *예측*하는 데 쓰이는 값이지, 설정을 *정하는* 데
    쓰이려면 "토큰이 몇 개일 때 epoch을 몇으로" 같은 근거가 필요한데 아직 없다.
    """
    rule = MODE_RULES[mode]
    return TrainingConfig(
        epochs=rule.epochs,
        batch_size=min(rule.batch_size, dataset.sample_count),
        learning_rate=rule.learning_rate,
        max_length=min(rule.max_length_cap, dataset.max_seq_length),
    )
