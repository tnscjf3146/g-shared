"""XGBoost가 실제로 쓸모가 있는지 확인할 비교 대상.

모델을 하나만 학습하고 MAPE를 적어 두면 그 숫자가 좋은지 나쁜지 알 수 없다.
15%가 훌륭한지 형편없는지는 **아무것도 학습하지 않았을 때 몇 %인가** 를 알아야
말할 수 있다. 그래서 항상 둘과 함께 잰다.

``median``
    학습 데이터의 중앙값을 언제나 답한다. 어떤 모델이든 최소한 이것보다는 나아야
    한다. 이기지 못하면 feature가 target을 설명하지 못한다는 뜻이다.

``throughput``
    ``effective_tokens x epochs / benchmark_tokens_per_sec``. Worker 등록 때 잰
    처리량으로 나눈 한 줄짜리 물리 추정이다.

    **이것을 production 공식으로 고정하지 않는다.** benchmark는 고정된 합성
    workload(batch 4, 길이 512, 135M 파라미터 모델)를 잰 값이라, 실제 Job의
    모델 크기·배치·시퀀스 길이가 다르면 그대로 맞지 않는다. 비교 대상으로만 쓴다.

    다만 이 baseline이 XGBoost와 비슷하거나 더 낫다면 그것은 보고할 사실이다.
    "XGBoost를 썼다"가 "XGBoost가 필요했다"를 뜻하지는 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np

#: throughput baseline이 쓰는 열. 없으면 baseline을 건너뛴다.
THROUGHPUT_INPUTS = ("effective_tokens", "epochs", "benchmark_tokens_per_sec")


@dataclass(frozen=True)
class BaselineScore:
    name: str
    mae_sec: float
    mape_percent: float

    def as_payload(self) -> dict[str, object]:
        return {
            "name": self.name,
            "maeSec": round(self.mae_sec, 4),
            "mapePercent": round(self.mape_percent, 4),
        }


def score(name: str, actual: np.ndarray, predicted: np.ndarray) -> BaselineScore:
    """초 단위 MAE와 상대오차. log 공간이 아니라 사람이 읽는 단위로 잰다."""
    actual = np.asarray(actual, dtype="float64")
    predicted = np.asarray(predicted, dtype="float64")
    errors = np.abs(actual - predicted)
    return BaselineScore(
        name=name,
        mae_sec=float(np.mean(errors)),
        mape_percent=float(np.mean(errors / actual) * 100.0),
    )


def median_prediction(train_target: np.ndarray, count: int) -> np.ndarray:
    """무엇을 묻든 같은 답. 평균이 아니라 중앙값인 이유는 실행 시간 분포가 한쪽으로
    길게 끌리기 때문이다 -- 평균은 가장 긴 몇 건에 끌려간다."""
    return np.full(count, float(np.median(train_target)), dtype="float64")


def throughput_prediction(rows: Sequence[Mapping[str, object]]) -> np.ndarray | None:
    """토큰 수를 benchmark 처리량으로 나눈 값. 쓸 수 없으면 ``None``.

    0이나 음수로 나누지 않는다. 그런 행이 하나라도 있으면 baseline 전체를 포기하고
    ``None`` 을 돌려준다 -- 일부만 계산한 평균은 다른 baseline과 비교할 수 없다.
    """
    values = []
    for row in rows:
        try:
            tokens = float(row["effective_tokens"])
            epochs = float(row["epochs"])
            rate = float(row["benchmark_tokens_per_sec"])
        except (KeyError, TypeError, ValueError):
            return None
        if not rate > 0 or not np.isfinite(rate):
            return None
        seconds = (tokens * epochs) / rate
        if not np.isfinite(seconds) or seconds <= 0:
            return None
        values.append(seconds)
    if not values:
        return None
    return np.asarray(values, dtype="float64")


def compare(
    *,
    train_target: np.ndarray,
    valid_target: np.ndarray,
    valid_rows: Sequence[Mapping[str, object]],
    model_predicted: np.ndarray,
) -> dict[str, object]:
    """세 방법의 점수와, XGBoost가 이겼는지.

    ``beatsBaselines`` 가 거짓인 채로 저장되는 것을 막지는 않는다. 학습을 실패시키는
    대신 사실을 기록한다 -- 지금 데이터에서 무엇이 이겼는지는 읽는 사람이 알아야
    할 결과이지, 감출 일이 아니다.
    """
    scores = [
        score("median", valid_target,
              median_prediction(train_target, len(valid_target))),
        score("xgboost", valid_target, model_predicted),
    ]
    throughput = throughput_prediction(valid_rows)
    if throughput is not None:
        scores.insert(1, score("throughput", valid_target, throughput))

    by_name = {item.name: item for item in scores}
    others = [item.mape_percent for item in scores if item.name != "xgboost"]
    return {
        "scores": [item.as_payload() for item in scores],
        "best": min(scores, key=lambda item: item.mape_percent).name,
        "beatsBaselines": bool(
            others and by_name["xgboost"].mape_percent < min(others)),
    }
