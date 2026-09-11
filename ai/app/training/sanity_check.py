"""부트스트랩으로 학습한 모델을 **실제 측정 두 건**에 대 본다.

이것은 평가가 아니다. 두 점으로 정확도를 말할 수는 없다. 그런데도 하는 이유는,
합성 데이터로 학습한 모델이 실제 값과 **몇 배나 어긋나는지**는 두 점으로도 알 수
있기 때문이다. 2배 틀리는 것과 200배 틀리는 것은 다른 이야기이고, 후자라면 합성
생성기의 물리 가정이 틀렸다는 뜻이라 고쳐야 한다.

``model_id`` 문제
-----------------
측정 두 건은 Qwen3로 돌렸는데 Backend 카탈로그에 Qwen3가 없어 가리킬 정수가
없다(:mod:`app.training.measured_runs` 참고). 그래서 하나를 골라 넣는 대신
**알려진 ``model_id`` 전부로 예측해 범위를 낸다.** 그 범위의 넓이 자체가 결과다 --
카탈로그 항목 하나가 예측을 몇 배 움직이는지 보여 주기 때문이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from app.scheduler.runtime_features import RuntimeFeatures


@dataclass(frozen=True)
class Comparison:
    run_id: str
    actual_sec: float
    predicted_low_sec: float
    predicted_high_sec: float

    @property
    def ratio_low(self) -> float:
        return self.predicted_low_sec / self.actual_sec

    @property
    def ratio_high(self) -> float:
        return self.predicted_high_sec / self.actual_sec

    def summary(self) -> str:
        return (
            f"{self.run_id}: actual {self.actual_sec:,.1f}s | "
            f"predicted {self.predicted_low_sec:,.1f}-{self.predicted_high_sec:,.1f}s "
            f"({self.ratio_low:.1f}x-{self.ratio_high:.1f}x)"
        )

    def as_payload(self) -> dict[str, object]:
        return {
            "runId": self.run_id,
            "actualSec": round(self.actual_sec, 3),
            "predictedLowSec": round(self.predicted_low_sec, 3),
            "predictedHighSec": round(self.predicted_high_sec, 3),
            "ratioLow": round(self.ratio_low, 4),
            "ratioHigh": round(self.ratio_high, 4),
        }


def compare_against_measured(
    predictor,
    runs: Sequence[Mapping[str, object]],
    model_ids: Sequence[object],
) -> list[Comparison]:
    """측정 기록마다 예측 범위를 낸다.

    ``model_ids`` 는 예측기가 아는 카테고리들이다. 측정 기록에 ``model_id`` 가
    있으면 그것만 쓰고, 없으면 전부 넣어 최솟값과 최댓값을 범위로 삼는다.
    """
    comparisons = []
    for run in runs:
        candidates = ([run["model_id"]] if run.get("model_id") is not None
                      else list(model_ids))
        if not candidates:
            continue
        rows = [
            RuntimeFeatures(
                benchmark_tokens_per_sec=float(run["benchmark_tokens_per_sec"]),
                vram_mb=int(run["vram_mb"]),
                sample_count=int(run["sample_count"]),
                effective_tokens=int(run["effective_tokens"]),
                avg_seq_length=float(run["avg_seq_length"]),
                max_seq_length=int(run["max_seq_length"]),
                epochs=int(run["epochs"]),
                batch_size=int(run["batch_size"]),
                max_length=int(run["max_length"]),
                gpu_model=str(run["gpu_model"]),
                model_id=str(model_id),
            )
            for model_id in candidates
        ]
        predicted = predictor.predict(rows)
        comparisons.append(Comparison(
            run_id=str(run.get("run_id", "?")),
            actual_sec=float(run["actual_runtime_sec"]),
            predicted_low_sec=min(predicted),
            predicted_high_sec=max(predicted),
        ))
    return comparisons
