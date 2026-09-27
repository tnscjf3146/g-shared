"""이 Job이 GPU에 요구하는 VRAM(MiB). 실측으로 채운 표 하나다.

지금까지 요구량은 Backend 카탈로그의 상수 하나(``workloads.min_vram_mb`` = 8192)였다.
모델도, batch도, 시퀀스 길이도 보지 않았다. 그래서 Qwen3-0.6B를 batch 4, 128 토큰으로
돌리는 가벼운 Job도 8 GiB를 요구했고, 6 GiB GPU는 어떤 설정에서도 후보가 되지 못했다.

**무엇으로 정하는가.** 모델, ``batchSize``, 그리고 실제 시퀀스 길이다. 실제 길이는
``min(trainingConfig.maxLength, dataset.maxSeqLength)`` 다. Runtime이 배치를 그 배치에서
가장 긴 샘플 길이까지만 채우므로(``system/runtime/qlora-sft`` 의 dynamic padding),
데이터가 짧으면 ``maxLength`` 를 아무리 크게 잡아도 GPU에 올라가는 길이는 짧다. 두 값은
스키마에서 모두 필수 양의 정수라, 없는 경우를 위한 기본값을 두지 않는다.

**모형이 아니라 표다.** 측정은 한 GPU에서 여섯 번 했다. 여섯 점으로 회귀식을 세우면 잰
적 없는 조합에 대해 그럴듯한 숫자를 지어내게 된다. 그래서 잰 칸만 쓴다.

* 칸은 (길이 상한, batch 상한) 이고, 값은 그 모서리에서 잰 최대치다. 요청은 자신을
  **덮는 칸 중 가장 작은 값**을 받는다 -- batch 3·길이 100은 B4·L128 칸, batch 6·길이
  100은 B8·L128 칸, batch 4·길이 300은 B4·L512 칸. VRAM은 batch와 길이가 늘면 줄지
  않으므로, 덮는 칸의 값은 요청의 실제 요구량보다 작지 않다.
* 덮는 칸이 없으면 (batch 16, batch 8·길이 512, 모르는 모델) **추정하지 않고**
  Backend가 보낸 ``requirements.minVramMb`` 를 그대로 쓴다. 이번 변경이 잰 적 없는
  영역의 동작을 바꾸지 않게 하기 위해서다.

**``minVramMb`` 의 의미가 바뀐다.** 지금 Backend가 보내는 8192는 모델의 진짜 하한이
아니라, 동적 계산이 없던 시절의 일괄 값이다(``base_models.min_vram_mb`` 가 비어 있어
``workloads.min_vram_mb`` 로 떨어진 값). 표에 칸이 있으면 그 칸의 값이 이 값보다
**우선한다** -- ``max(8192, 칸)`` 으로 하면 0.6B·B4·L128의 4608이 다시 8192가 되어
6 GiB GPU는 여전히 후보에 오르지 못한다. 칸이 없을 때만 ``minVramMb`` 가 요구량이다.

측정 출처
--------
2026-09-21, VRAM calibration E01~E06. Production QLoRA Runtime을 그대로 썼다.

* GPU: NVIDIA GeForce RTX 4070 Laptop GPU, 물리 VRAM 8188 MiB (WSL2)
* torch 2.12.1+cu126 · transformers 5.17.0 · peft 0.20.0 · bitsandbytes 0.50.2
* NF4 double quantization · LoRA r16/α32/dropout 0.05 · all-linear ·
  paged_adamw_8bit · gradient checkpointing 꺼짐 · gradient accumulation 1 ·
  dynamic padding

==== ========== ===== ======= ============== ============= ========
실험  모델        batch 실제 max peak allocated peak reserved 요구량
==== ========== ===== ======= ============== ============= ========
E01  Qwen3-0.6B   4     127      3,541 MiB      3,732 MiB  4,608
E02  Qwen3-0.6B   8     127      5,859 MiB      6,202 MiB  7,168
E03  Qwen3-0.6B   4     511     10,539 MiB     11,228 MiB 13,312
E04  Qwen3-1.7B   4     127      5,913 MiB      6,122 MiB  7,168
E05  Qwen3-1.7B   8     127      9,157 MiB      9,552 MiB 11,264
E06  Qwen3-1.7B   4     511     15,764 MiB     16,542 MiB 19,456
==== ========== ===== ======= ============== ============= ========

요구량 = ``ceil((reserved + max(reserved × 0.15, 512)) / 512) × 512``. 기준이
allocated가 아니라 reserved인 것은 GPU가 실제로 비워 줘야 하는 양이 할당기가 잡아 둔
양이기 때문이다. E03·E05·E06은 reserved가 물리 VRAM(8188)을 넘는다 -- WSL2 드라이버가
OOM 대신 시스템 메모리로 넘겨 끝까지 돌았을 뿐, 8 GiB 카드에 들어간 것이 아니다.

``benchmarkPeakVramMb`` 는 쓰지 않는다. Worker 등록 때 도는 합성 벤치마크(GPT-2-small
크기, 고정 설정)의 최고치라 이 Job의 요구량과 관계가 없다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

#: Backend ``base_models.model_id`` (AUTO_INCREMENT PK). V8이 테이블을 새로 만들며
#: ``qwen2.5-7b`` 를 1번으로 넣었고, V12가 ``qwen3-0.6b`` 와 ``qwen3-1.7b`` 를 이
#: 순서로 한 줄씩 넣었다. 그 뒤로 base_models에 INSERT하는 마이그레이션은 없다.
#: PK가 바뀌면(운영 DB에서 행을 지웠다 다시 넣으면) 이 표는 조용히 legacy로 떨어진다
#: -- 틀린 값을 쓰지는 않는다.
QWEN3_0_6B_MODEL_ID = 2
QWEN3_1_7B_MODEL_ID = 3

#: 모델별 측정 칸: (시퀀스 길이 상한, batch 상한, 요구 VRAM MiB). 요구량이 작은 칸부터.
CALIBRATED_REQUIREMENTS_MIB: dict[int, tuple[tuple[int, int, int], ...]] = {
    QWEN3_0_6B_MODEL_ID: (
        (128, 4, 4608),    # E01
        (128, 8, 7168),    # E02
        (512, 4, 13312),   # E03
    ),
    QWEN3_1_7B_MODEL_ID: (
        (128, 4, 7168),    # E04
        (128, 8, 11264),   # E05
        (512, 4, 19456),   # E06
    ),
}


class VramRequirementSource(str, Enum):
    """요구량이 어디서 왔는가. 응답에 싣지 않는다 -- 테스트와 디버깅용이다."""

    CALIBRATED_RULE = "CALIBRATED_RULE"
    LEGACY_FALLBACK = "LEGACY_FALLBACK"


@dataclass(frozen=True)
class VramRequirement:
    required_vram_mb: int
    source: VramRequirementSource


def effective_sequence_length(max_length: int, max_seq_length: int) -> int:
    """GPU에 실제로 올라가는 가장 긴 시퀀스. 잘림(maxLength)과 데이터(maxSeqLength) 중 짧은 쪽."""
    return min(max_length, max_seq_length)


def estimate_required_vram_mb(
    *,
    model_id: int,
    batch_size: int,
    max_length: int,
    max_seq_length: int,
    legacy_min_vram_mb: int,
) -> VramRequirement:
    """측정 칸이 덮으면 그 값, 아니면 Backend가 보낸 ``legacy_min_vram_mb``.

    칸의 값은 ``legacy_min_vram_mb`` 보다 작아도 그대로 쓴다. 모듈 설명 참고.
    """
    length = effective_sequence_length(max_length, max_seq_length)
    for length_ceiling, batch_ceiling, required in CALIBRATED_REQUIREMENTS_MIB.get(model_id, ()):
        if length <= length_ceiling and batch_size <= batch_ceiling:
            return VramRequirement(required, VramRequirementSource.CALIBRATED_RULE)
    return VramRequirement(legacy_min_vram_mb, VramRequirementSource.LEGACY_FALLBACK)
