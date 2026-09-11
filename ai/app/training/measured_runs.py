"""실제로 측정된 실행 기록. 한 건도 추정이 아니다.

숫자를 JSONL에 직접 적지 않고 이 모듈에서 만드는 이유는, **각 값이 어디서 나왔는지
값 옆에 적어 두기 위해서**다. 데이터 파일에는 숫자만 남고, 반년 뒤에 그 숫자가
측정값인지 누가 채워 넣은 것인지 확인할 방법이 없다.

출처
----
전부 2026-09-10~11에 System 1번(QLoRA Runtime Image) 작업에서 같은 기계로
측정했다.

* **GPU** NVIDIA GeForce RTX 4070 Laptop GPU, VRAM 8,188 MiB (nvidia-smi),
  Compute Capability 8.9, driver 596.36, WSL2 + Docker 29.7.2
* **benchmark_tokens_per_sec** Worker가 등록 때 쓰는 바로 그 프로그램
  (``gshared_worker.benchmark_program``, ``gshared-benchmark-v1``)을 고정 이미지
  안에서 돌린 결과. 세 run의 중앙값 **29,048.535**.
  (run: 29,957.199 / 28,369.277 / 29,048.535, peak VRAM 2,175 MB)
  이 값을 짐작으로 채우지 않으려고 benchmark를 실제로 돌렸다.
* **토큰 통계** 학습에 쓴 것과 같은 tokenizer(Qwen3-0.6B), 같은 prompt template,
  같은 절단 길이로 다시 센 값이다.
* **actual_runtime_sec** 컨테이너가 스스로 기록한 ``training_metrics.json`` 의
  ``runtimeSec``. 모델 적재부터 어댑터 재적재 검증까지를 포함한다.

왜 두 건뿐인가
--------------
System 1번에서 실행한 것은 여섯 번이지만, **끝까지 돌린 Job은 둘뿐**이다. 나머지
넷은 ``--max-steps`` 로 중간에서 끊었다. 그 넷도 :data:`CALIBRATION_RUNS` 에
기록하지만 학습 target으로는 쓰지 않는다 -- 이유는
:mod:`app.training.provenance` 에 적었다. 요약하면, 11개 feature 안에 "몇 step에서
끊었는가"가 없어서 **끝까지 돌린 행과 구분이 되지 않기 때문**이다.

이 두 건을 읽을 때의 주의
-------------------------
**두 건 모두 VRAM이 모자란 상태에서 측정됐다.** 카드의 물리 VRAM은 8,187 MiB인데
peak allocated가 9,783 / 10,282 MB다. OOM이 나지 않은 것은 WSL2 NVIDIA 드라이버가
시스템 메모리로 넘겨받기 때문이고, 그 대가로 step당 시간이 1.9초에서 2.9초로
나빠졌다. 지배적인 비용은 가중치가 아니라 **vocab 151,936짜리 logit** 이다.

그래서 이 두 건의 실효 처리량(약 590 tokens/sec)은 같은 GPU가 여유 있는 설정에서
낼 수 있는 값보다 훨씬 낮다. 합성 생성기는 이 현상을 모형에 넣지 않았고, 그래서
1,440건 쪽을 실제보다 빠르게 예측한다. 생성기를 이 두 점에 맞춰 더 깎지 않은
이유가 이것이다 -- 맞춰야 할 대상은 두 점이 아니라 물리이고, vocab이 VRAM을
지배한다는 사실은 아직 모형에 없다.

왜 학습에 들어가지 않는가
-------------------------
두 건은 ``model_id`` 가 없다. Backend ``base_models`` 에 Qwen3 항목이 아직 없어서
이 모델을 가리킬 정수가 존재하지 않는다. **없는 PK를 지어내지 않는다.** 그래서
:func:`trainable_rows` 가 빈 목록을 돌려주고, 두 건은 학습 대신
:mod:`app.training.sanity_check` 의 대조용으로 쓰인다. Backend가 Qwen3를 seed하는
날 ``model_id`` 만 채우면 그대로 학습 데이터가 된다.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.training.provenance import Provenance

MEASURED_FILE_NAME = "runtime_measured.jsonl"

#: 이 기계. 모든 행이 공유한다.
GPU_MODEL = "NVIDIA GeForce RTX 4070 Laptop GPU"
GPU_VRAM_MB = 8188
#: gshared-benchmark-v1 세 run의 중앙값. 짐작이 아니라 측정값이다.
BENCHMARK_TOKENS_PER_SEC = 29048.535

#: Backend ``base_models`` 에 Qwen3가 없다. 지어내지 않고 비워 둔다.
MODEL_ID_PENDING = None

_BASE = {
    "gpu_model": GPU_MODEL,
    "vram_mb": GPU_VRAM_MB,
    "benchmark_tokens_per_sec": BENCHMARK_TOKENS_PER_SEC,
    "model_id": MODEL_ID_PENDING,
    "model_ref": "Qwen/Qwen3-0.6B",
    "model_revision": "c1899de289a04d12100db370d81485cdf75e47ca",
    "docker_image_id": 1,
}


#: 끝까지 돌린 Job. ``--max-steps`` 없이 실행했다.
MEASURED_RUNS: tuple[dict, ...] = (
    {
        **_BASE,
        "run_id": "quick-demo-240",
        "sample_count": 240,
        # 잘린 뒤의 토큰 수 합계 19,635. effective는 배치 안에서 가장 긴 것에
        # 맞춰 채운 뒤 GPU에 올라간 양이라 이보다 크다.
        "total_tokens": 19635,
        "effective_tokens": 25536,
        "avg_seq_length": 81.812,
        "max_seq_length": 118,
        "epochs": 1,
        "batch_size": 16,
        "max_length": 512,
        "total_steps": 15,
        "actual_runtime_sec": 33.441,
        "training_runtime_sec": 28.485,
        "peak_vram_mb": 9783,
        "provenance": Provenance.MEASURED.value,
    },
    {
        **_BASE,
        "run_id": "quick-demo-1440",
        "sample_count": 1440,
        "total_tokens": 117561,
        "effective_tokens": 154848,
        "avg_seq_length": 81.64,
        "max_seq_length": 123,
        "epochs": 1,
        "batch_size": 16,
        "max_length": 512,
        "total_steps": 90,
        "actual_runtime_sec": 267.959,
        "training_runtime_sec": 262.251,
        "peak_vram_mb": 10282,
        "provenance": Provenance.MEASURED.value,
    },
)


#: 중간에서 끊은 실행. **학습 target이 아니다.**
#:
#: 기록해 두는 이유는 두 가지다. 버리면 다시 재야 하고, 남겨 두면 누군가 나중에
#: "왜 이 값은 안 썼는가"를 물었을 때 답이 파일 안에 있다. ``cut_at_steps`` 가
#: 그 답이고, 이 열은 11개 feature 계약에 **없다** -- 그래서 학습에 쓸 수 없다.
CALIBRATION_RUNS: tuple[dict, ...] = (
    {
        **_BASE,
        "run_id": "smoke-0.6b",
        "sample_count": 16, "epochs": 1, "batch_size": 4, "max_length": 512,
        "cut_at_steps": 3,
        "actual_runtime_sec": 7.150, "training_runtime_sec": 2.289,
        "peak_vram_mb": 3060,
        "provenance": Provenance.CALIBRATION.value,
    },
    {
        **_BASE,
        "run_id": "smoke-1.7b",
        "model_ref": "Qwen/Qwen3-1.7B",
        "model_revision": "70d244cc86ccca08cf5af4e1e306ecf908b1ad5e",
        "sample_count": 16, "epochs": 1, "batch_size": 4, "max_length": 512,
        "cut_at_steps": 3,
        "actual_runtime_sec": 11.070, "training_runtime_sec": 2.584,
        "peak_vram_mb": 5238,
        "provenance": Provenance.CALIBRATION.value,
    },
    {
        **_BASE,
        "run_id": "standard-calibration-40",
        "model_ref": "Qwen/Qwen3-1.7B",
        "model_revision": "70d244cc86ccca08cf5af4e1e306ecf908b1ad5e",
        "sample_count": 400, "epochs": 3, "batch_size": 8, "max_length": 1024,
        "cut_at_steps": 40,
        "actual_runtime_sec": 143.387, "training_runtime_sec": 137.321,
        "peak_vram_mb": 8685,
        "provenance": Provenance.CALIBRATION.value,
    },
    {
        **_BASE,
        "run_id": "standard-calibration-100",
        "model_ref": "Qwen/Qwen3-1.7B",
        "model_revision": "70d244cc86ccca08cf5af4e1e306ecf908b1ad5e",
        "sample_count": 400, "epochs": 3, "batch_size": 8, "max_length": 1024,
        "cut_at_steps": 100,
        "actual_runtime_sec": 364.292, "training_runtime_sec": 359.714,
        "peak_vram_mb": 8685,
        "provenance": Provenance.CALIBRATION.value,
    },
)

ALL_RUNS: tuple[dict, ...] = MEASURED_RUNS + CALIBRATION_RUNS


def trainable_rows() -> list[dict]:
    """지금 학습에 넣을 수 있는 실측 행. 현재는 빈 목록이다.

    ``model_id`` 가 없는 행은 제외한다. XGBoost는 ``None`` 을 카테고리 하나로
    삼아 아무 불평 없이 학습하고, 그러면 "카탈로그에 없는 모델"이라는 범주가
    생겨 버린다. Backend가 Qwen3를 seed하면 이 함수가 저절로 두 건을 돌려준다.
    """
    return [row for row in MEASURED_RUNS if row.get("model_id") is not None]


def write_measured(directory: Path) -> Path:
    """측정 기록 전체를 JSONL로. calibration도 함께 쓴다."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / MEASURED_FILE_NAME
    with path.open("w", encoding="utf-8") as handle:
        for row in ALL_RUNS:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return path
