"""Runtime 예측 모델을 학습하고 저장한다.

학습과 예측이 다른 파일에 있는 이유는 도는 곳이 다르기 때문이다. 학습은 사람이
가끔 돌리는 배치 작업이고, 예측은 매 요청마다 도는 서비스다. 한 파일에 두면
서비스가 학습 코드와 학습 데이터까지 들고 다니게 된다.

**Target은 실제 실행 시간(초)뿐이다.** 비용도, 토큰 가격도, benchmark 점수도 아니다.

Leakage 방지가 이 파일의 조용한 책임이다. ``actual_runtime_sec`` 는 y로만 가고
X에는 절대 들어가지 않으며, ``worker_id`` 같은 식별자는 애초에
:data:`~app.scheduler.runtime_features.RUNTIME_FEATURE_NAMES` 에 없다. 열은 그
목록으로만 만들어지므로 데이터에 무엇이 더 들어 있든 feature가 되지 않는다.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import train_test_split
from xgboost import XGBRegressor

from app.scheduler.runtime_features import (
    CATEGORICAL_FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
    RUNTIME_FEATURE_NAMES,
    TARGET_NAME,
    CategoryMapping,
)
from app.training.runtime_dataset import TRAINING_DATA_TYPE

#: 저장되는 파일 이름. 예측 쪽도 이 상수를 쓴다.
MODEL_FILE_NAME = "runtime_model.json"
METADATA_FILE_NAME = "runtime_model.metadata.json"

#: 예측 응답에 실려 나가는 버전. 한 곳에서만 정의하고 metadata에 기록한다.
MODEL_VERSION = "runtime-xgb-v1"

#: 재현성을 위해 고정한다. 같은 데이터와 같은 seed면 같은 모델이 나온다.
RANDOM_STATE = 20250908

#: MVP다. Hyperparameter 탐색은 하지 않는다 -- 지금 데이터는 synthetic이라
#: 탐색해서 좋아진 숫자가 실제 정확도를 뜻하지 않는다. 실제 Job History가
#: 쌓인 뒤에 할 일이다. 한 곳에만 적어 두고 metadata에 그대로 기록한다.
XGB_PARAMS: Mapping[str, object] = {
    "n_estimators": 400,
    "max_depth": 6,
    "learning_rate": 0.05,
    "subsample": 0.9,
    "colsample_bytree": 0.9,
    "min_child_weight": 2,
    "objective": "reg:squarederror",
    "random_state": RANDOM_STATE,
    "enable_categorical": True,
    "tree_method": "hist",
}

#: 실행 시간은 log를 씌워 학습한다.
#:
#: 이유는 target의 모양이다. 짧은 작업은 1분, 큰 작업은 며칠이라 범위가 수천 배로
#: 벌어지는데, 초 단위 원본에 제곱오차를 쓰면 가장 긴 작업 몇 개가 손실을 거의
#: 다 차지하고 짧은 작업은 몇 배씩 틀려도 학습에 반영되지 않는다.
#:
#: 스케줄러가 신경 쓰는 오차도 상대오차다. 3분짜리 작업을 5분 틀리면 쓸모없는
#: 예측이고 100시간짜리를 5분 틀리는 것은 아무 문제가 아니다. log 공간의 제곱오차가
#: 그 관점과 맞는다. 실행 시간의 흔들림 자체도 덧셈이 아니라 곱셈으로 온다 --
#: "30초 더"가 아니라 "5% 더 느림"이다.
#:
#: 학습과 예측 양쪽에 똑같이 적용되며, metadata에 기록되어 예측할 때 읽는다.
#: 예측을 낸 뒤에 규칙으로 숫자를 고치는 것과는 다른 이야기다.
TARGET_TRANSFORM = "log"

#: 이보다 적으면 검증셋을 떼어도 숫자만 나올 뿐 의미가 없다.
MIN_ROWS_FOR_VALIDATION = 50
VALIDATION_FRACTION = 0.2


class TrainingDataError(ValueError):
    """학습 데이터가 모델을 만들 수 있는 모양이 아니다."""


@dataclass(frozen=True)
class TrainingReport:
    """학습 한 번의 결과. 사람이 읽고 metadata에 기록된다."""

    row_count: int
    train_rows: int
    validation_rows: int
    mae_sec: float | None
    mape_percent: float | None
    model_path: Path
    metadata_path: Path
    #: synthetic 데이터로 잰 값인지. metrics를 읽는 사람이 반드시 알아야 한다.
    training_data_type: str

    def summary(self) -> str:
        if self.mae_sec is None:
            return f"{self.row_count} rows, validation skipped (too few rows)"
        return (
            f"{self.row_count} rows | MAE {self.mae_sec:,.1f} sec"
            f" | MAPE {self.mape_percent:.2f}%"
        )


def build_frame(rows: Sequence[Mapping[str, object]]) -> pd.DataFrame:
    """행들을 열 순서가 고정된 DataFrame으로.

    :data:`RUNTIME_FEATURE_NAMES` 에 있는 열만 만든다. 데이터에
    ``actual_runtime_sec`` 나 ``worker_id`` 가 들어 있어도 X에 들어가지 않는 이유가
    이것이다 -- 데이터를 보고 열을 정하지 않고, 정해진 열을 데이터에서 꺼낸다.
    """
    if not rows:
        raise TrainingDataError("the training dataset is empty")

    missing = sorted(set(RUNTIME_FEATURE_NAMES) - set(rows[0]))
    if missing:
        raise TrainingDataError(f"rows are missing feature(s): {', '.join(missing)}")

    return pd.DataFrame(
        [{name: row[name] for name in RUNTIME_FEATURE_NAMES} for row in rows],
        columns=list(RUNTIME_FEATURE_NAMES),
    )


def encode(frame: pd.DataFrame, categories: CategoryMapping) -> pd.DataFrame:
    """문자열 열을 카테고리 코드로. 순서 있는 숫자로 취급되지 않도록 dtype을 준다.

    XGBoost는 ``category`` dtype인 열을 값의 집합으로 쪼갠다. 정수로만 두면
    "3보다 작은가"처럼 순서로 쪼개는데, GPU 이름 사이에 순서는 없다.
    """
    encoded = frame.copy()
    for name in CATEGORICAL_FEATURE_NAMES:
        codes = encoded[name].map(lambda value: categories.code_of(name, value))
        known = len(categories.known_values(name))
        encoded[name] = pd.Categorical(codes, categories=range(known))
    for name in RUNTIME_FEATURE_NAMES:
        if name not in CATEGORICAL_FEATURE_NAMES:
            encoded[name] = encoded[name].astype("float64")
    return encoded


def _targets(rows: Sequence[Mapping[str, object]]) -> np.ndarray:
    values = []
    for index, row in enumerate(rows):
        if TARGET_NAME not in row:
            raise TrainingDataError(f"row {index} has no {TARGET_NAME}")
        value = row[TARGET_NAME]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TrainingDataError(f"row {index}: {TARGET_NAME} must be a number")
        number = float(value)
        if not math.isfinite(number):
            raise TrainingDataError(f"row {index}: {TARGET_NAME} must be finite")
        if number <= 0:
            # 0초에 끝난 학습은 없다. MAPE의 분모가 되기도 한다.
            raise TrainingDataError(
                f"row {index}: {TARGET_NAME} must be greater than zero, got {number}")
        values.append(number)
    return np.asarray(values, dtype="float64")


def train_runtime_model(
    rows: Sequence[Mapping[str, object]],
    *,
    models_directory: Path,
    training_data_type: str = TRAINING_DATA_TYPE,
) -> TrainingReport:
    """학습하고, 저장하고, 무엇으로 학습했는지 함께 남긴다."""
    frame = build_frame(rows)
    target = _targets(rows)
    categories = CategoryMapping.learned_from(rows)
    encoded = encode(frame, categories)

    if len(rows) >= MIN_ROWS_FOR_VALIDATION:
        x_train, x_valid, y_train, y_valid = train_test_split(
            encoded, target, test_size=VALIDATION_FRACTION, random_state=RANDOM_STATE)
    else:
        # 행이 적을 때 숫자를 채우려고 의미 없는 검증셋을 떼지 않는다.
        x_train, y_train = encoded, target
        x_valid = y_valid = None

    model = XGBRegressor(**XGB_PARAMS)
    model.fit(x_train, np.log(y_train))

    mae = mape = None
    if x_valid is not None:
        # 지표는 초 단위 원래 값으로 되돌려 잰다. log 공간의 오차는 사람이 읽을 수
        # 있는 값이 아니고, 보고서에 적히는 숫자는 초여야 한다.
        predicted = np.exp(model.predict(x_valid))
        mae = float(mean_absolute_error(y_valid, predicted))
        mape = float(np.mean(np.abs((y_valid - predicted) / y_valid)) * 100.0)

    models_directory.mkdir(parents=True, exist_ok=True)
    model_path = models_directory / MODEL_FILE_NAME
    metadata_path = models_directory / METADATA_FILE_NAME
    # XGBoost 자체 포맷. pickle로 객체를 통째로 저장하면 라이브러리 버전이 바뀌는
    # 날 못 읽고, 신뢰할 수 없는 파일을 여는 위험도 같이 따라온다.
    model.save_model(model_path)

    metadata = {
        "modelVersion": MODEL_VERSION,
        "featureSchemaVersion": FEATURE_SCHEMA_VERSION,
        "featureNames": list(RUNTIME_FEATURE_NAMES),
        "categoricalFeatureNames": list(CATEGORICAL_FEATURE_NAMES),
        "categoryMappings": categories.as_payload(),
        "target": TARGET_NAME,
        "targetTransform": TARGET_TRANSFORM,
        "trainedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "trainingDataType": training_data_type,
        "rowCount": len(rows),
        "trainRows": int(len(x_train)),
        "validationRows": 0 if x_valid is None else int(len(x_valid)),
        "xgbParams": dict(XGB_PARAMS),
        "metrics": {
            "maeSec": mae,
            "mapePercent": mape,
            # metrics를 읽는 사람이 이것을 못 보고 지나가지 않도록 metadata 안에
            # 같이 넣는다. synthetic 데이터의 오차는 파이프라인이 도는지를 말할 뿐
            # 실행 시간을 얼마나 맞히는지를 말하지 않는다.
            "note": (
                "bootstrap synthetic validation -- pipeline check only, "
                "not production accuracy"
                if training_data_type == TRAINING_DATA_TYPE
                else "measured from real job history"
            ),
        },
    }
    metadata_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

    return TrainingReport(
        row_count=len(rows),
        train_rows=int(len(x_train)),
        validation_rows=0 if x_valid is None else int(len(x_valid)),
        mae_sec=mae,
        mape_percent=mape,
        model_path=model_path,
        metadata_path=metadata_path,
        training_data_type=training_data_type,
    )
