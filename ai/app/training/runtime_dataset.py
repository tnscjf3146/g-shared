"""**SYNTHETIC** 부트스트랩 데이터. 실제 Job 실행 기록이 아니다.

무엇을 위한 것인가
------------------
저장소 전체를 찾아봤지만 실제 Job 실행 기록이 없다 -- CSV도, Backend의 runtime
컬럼도, PoC의 실행 로그도 없다. 있는 것은 Worker 등록 때 재는 GPU benchmark뿐이고
그건 실행 시간이 아니다.

그래서 이 파일은 **학습 파이프라인과 API가 실제로 도는지 확인하기 위한** 데이터를
만든다. 정확도를 증명하기 위한 것이 아니다. 이 데이터로 학습한 모델의 오차는
"내가 만든 공식을 XGBoost가 얼마나 잘 따라 했는가"이지 "실행 시간을 얼마나 잘
맞히는가"가 아니다. 실제 Job History가 쌓이면 이 파일은 통째로 교체된다.

완전히 무작위로 만들지 않은 이유
--------------------------------
target이 무작위면 모델이 배울 관계가 없어 파이프라인이 도는지조차 확인할 수 없다.
그래서 아래 관계는 물리적으로 말이 되게 만든다.

* 처리할 토큰이 많을수록, epoch가 많을수록 오래 걸린다.
* 같은 작업이면 ``benchmarkTokensPerSec`` 가 높은 GPU가 빠르다.
* 배치가 너무 작으면 GPU를 못 채워 효율이 떨어진다.
* 시퀀스가 길수록 attention 때문에 토큰당 비용이 오른다.
* 큰 모델일수록 토큰당 계산량이 많다.

**VRAM은 실행 시간과 단순 반비례하지 않게** 만든다. 설정이 들어갈 만큼 넉넉하면
더 많다고 빨라지지 않고, 빠듯해질 때만 느려진다. 실제로 그렇게 동작하기 때문이다.

``gpuModel`` 이름으로 실행 시간을 정하지 않는다. GPU가 실행 시간에 영향을 주는
경로는 측정된 ``benchmarkTokensPerSec`` 하나뿐이고, 이름은 그 값을 뽑을 분포를
고르는 데만 쓴다.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from pathlib import Path

#: 파일 이름에 synthetic이 들어간다. 나중에 이 디렉터리를 뒤지는 사람이 파일명만
#: 보고도 실측 데이터가 아니라는 것을 알아야 한다.
BOOTSTRAP_FILE_NAME = "runtime_bootstrap_synthetic.jsonl"

#: metadata에 기록되는 값. 이 모델이 무엇으로 학습됐는지 나중에 알 수 있도록.
TRAINING_DATA_TYPE = "synthetic-bootstrap"

#: 재현성. 같은 seed면 같은 데이터가 나온다.
DEFAULT_SEED = 20250908

#: Worker Benchmark가 재는 고정 workload의 모델 크기(파라미터). benchmark가 이
#: 크기 기준의 초당 토큰이므로, 더 큰 모델을 학습하면 그만큼 느려진다.
BENCHMARK_MODEL_PARAMS = 135_000_000


@dataclass(frozen=True)
class _Gpu:
    """SYNTHETIC. benchmark 값의 분포만 정한다. 실행 시간을 직접 정하지 않는다."""

    name: str
    vram_mb: int
    #: 측정될 법한 tokens/sec의 범위. RTX 4070 Laptop만 실제 측정값(36,521.7)에
    #: 맞춰 두었고 나머지는 그럴듯한 폭을 준 것이다.
    benchmark_low: float
    benchmark_high: float


_GPUS: tuple[_Gpu, ...] = (
    _Gpu("NVIDIA GeForce RTX 3060", 12288, 15_500, 18_500),
    _Gpu("NVIDIA GeForce RTX 4060", 8188, 19_000, 23_000),
    _Gpu("NVIDIA GeForce RTX 4070 Laptop GPU", 8188, 35_200, 37_400),
    _Gpu("NVIDIA GeForce RTX 4080", 16376, 52_000, 58_000),
    _Gpu("NVIDIA GeForce RTX 4090", 24564, 68_000, 76_000),
)


@dataclass(frozen=True)
class _Model:
    """SYNTHETIC. 파라미터 수는 모델의 사실이고, 여기서는 토큰당 비용을 정한다."""

    model_id: str
    params: int


_MODELS: tuple[_Model, ...] = (
    _Model("qwen2.5-1.5b", 1_500_000_000),
    _Model("qwen2.5-3b", 3_000_000_000),
    _Model("qwen2.5-7b", 7_000_000_000),
)

#: 현재 승인된 이미지는 하나다. 계약에는 있지만 feature에는 들어가지 않는 이유가
#: 이것이다 -- 모든 행에서 같은 값인 열은 정보가 없다.
DOCKER_IMAGE_ID = "qlora-sft-v1"


def _effective_rate(
    *,
    benchmark_tokens_per_sec: float,
    params: int,
    batch_size: int,
    max_length: int,
    vram_mb: int,
) -> float:
    """이 설정에서 실제로 나올 초당 토큰. benchmark 값에서 출발해 깎는다."""
    # 큰 모델일수록 토큰당 계산이 많다. 완전히 선형은 아니다 -- 커널이 커질수록
    # GPU를 더 잘 채우기 때문에 지수를 1보다 조금 작게 둔다.
    model_slowdown = (params / BENCHMARK_MODEL_PARAMS) ** 0.85

    # 배치가 작으면 GPU가 논다. 어느 지점부터는 더 키워도 나아지지 않는다.
    batch_efficiency = 0.55 + 0.45 * min(1.0, batch_size / 8.0)

    # 시퀀스가 길수록 attention 비용이 토큰당으로도 오른다.
    sequence_penalty = (max_length / 512.0) ** 0.15

    # VRAM: 넉넉하면 아무 영향이 없고, 빠듯해질 때만 느려진다. 반비례가 아니다.
    needed_mb = (params / 1_000_000) * 0.6 + batch_size * max_length * 0.012
    pressure = needed_mb / vram_mb
    if pressure <= 0.75:
        vram_penalty = 1.0
    else:
        # 여기부터 gradient checkpointing이나 마이크로배치로 넘어가며 느려진다.
        vram_penalty = 1.0 + 2.2 * (pressure - 0.75)

    return (
        benchmark_tokens_per_sec
        * batch_efficiency
        / (model_slowdown * sequence_penalty * vram_penalty)
    )


def _startup_overhead(params: int, rng: random.Random) -> float:
    """컨테이너 기동과 가중치 로딩. 큰 모델일수록 오래 걸린다."""
    return 45.0 + (params / 1_000_000_000) * 12.0 + rng.uniform(-8.0, 8.0)


def build_rows(count: int = 1200, seed: int = DEFAULT_SEED) -> list[dict[str, object]]:
    """SYNTHETIC 학습 행 ``count`` 개. 같은 seed면 항상 같은 결과."""
    rng = random.Random(seed)
    rows: list[dict[str, object]] = []

    for _ in range(count):
        gpu = rng.choice(_GPUS)
        model = rng.choice(_MODELS)
        benchmark = rng.uniform(gpu.benchmark_low, gpu.benchmark_high)

        sample_count = rng.randint(500, 60_000)
        avg_seq_length = rng.uniform(80.0, 900.0)
        max_seq_length = int(avg_seq_length * rng.uniform(1.5, 4.0)) + 1
        max_length = rng.choice((256, 512, 1024, 2048))
        batch_size = rng.choice((1, 2, 4, 8, 16, 32))
        epochs = rng.randint(1, 5)

        # GPU에 실제로 올라가는 토큰: 샘플마다 max_length로 자르거나 채운다.
        per_sample = min(avg_seq_length * rng.uniform(1.05, 1.35), float(max_length))
        effective_tokens = max(1, int(sample_count * per_sample))
        total_tokens = max(effective_tokens, int(sample_count * avg_seq_length))

        rate = _effective_rate(
            benchmark_tokens_per_sec=benchmark,
            params=model.params,
            batch_size=batch_size,
            max_length=max_length,
            vram_mb=gpu.vram_mb,
        )
        runtime = (effective_tokens * epochs) / rate + _startup_overhead(model.params, rng)
        # 같은 설정을 두 번 돌려도 완전히 같지는 않다. 열, 다른 프로세스, 스케줄링.
        runtime *= math.exp(rng.gauss(0.0, 0.05))

        rows.append({
            "gpu_model": gpu.name,
            "vram_mb": gpu.vram_mb,
            "benchmark_tokens_per_sec": round(benchmark, 3),
            "sample_count": sample_count,
            "total_tokens": total_tokens,
            "effective_tokens": effective_tokens,
            "avg_seq_length": round(avg_seq_length, 3),
            "max_seq_length": max_seq_length,
            "epochs": epochs,
            "batch_size": batch_size,
            "max_length": max_length,
            "model_id": model.model_id,
            "docker_image_id": DOCKER_IMAGE_ID,
            "actual_runtime_sec": round(runtime, 3),
        })

    return rows


def write_bootstrap(directory: Path, count: int = 1200, seed: int = DEFAULT_SEED) -> Path:
    """행들을 JSONL로 쓴다. 경로를 돌려준다."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / BOOTSTRAP_FILE_NAME
    with path.open("w", encoding="utf-8") as handle:
        for row in build_rows(count, seed):
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def read_rows(path: Path) -> list[dict[str, object]]:
    """JSONL 한 줄에 한 행. 빈 줄은 건너뛴다."""
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows
