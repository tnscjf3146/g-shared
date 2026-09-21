"""저장된 모델을 불러 GPU 후보들의 실행 시간을 예측한다.

학습 코드와 떨어져 있는 이유는 :mod:`app.training.runtime_trainer` 에 적었다.
여기서는 절대 학습하지 않는다.

이 파일이 지키는 것 두 가지.

* **열 순서와 카테고리 표는 학습 때 쓴 것을 그대로 쓴다.** metadata에서 읽어 오고,
  지금 코드의 feature schema와 다르면 불러오지 않는다. 다른 열로 학습된 모델에
  지금 열을 넣으면 예외 없이 그럴듯한 오답이 나온다.
* **예측이 아닌 값은 예측으로 내보내지 않는다.** NaN, 무한대, 0 이하는 모두 오류다.
  없는 답 대신 그럴듯한 숫자를 주면 Backend는 구분할 방법이 없다.

모델은 log를 씌운 실행 시간을 맞히도록 학습됐으므로(이유는
:mod:`app.training.runtime_trainer`) 여기서 다시 지수를 취한다. 그 변환이
metadata에 적혀 있는지 확인하고 시작하는 이유는, log로 학습된 모델의 답을 그대로
초로 읽으면 하루짜리 작업이 11초로 나오기 때문이다 -- 예외 하나 없이.

**본 적 없는 GPU.** ``gpu_model`` 은 카테고리 열이라, 학습 때 없던 이름(예: 새로
붙은 RTX 4050 Laptop)은 코드가 없다. 이름 하나 때문에 추천 전체를 거절하지 않되,
근거 없는 숫자도 넣지 않는다. 대신 그 Worker가 **실제로 잰**
``benchmarkTokensPerSec`` 과 가장 가까운 학습 카테고리를 그 행의 예측에만 대신
쓴다(:meth:`RuntimePredictor.resolve_gpu_category`). 나머지 열 -- benchmark 값
자체, vramMb -- 는 그 Worker의 실제 값 그대로다. 요청·응답의 ``gpuModel`` 은 바뀌지
않는다. 이름으로 고르지 않는다: "4050이니 4060" 같은 규칙은 다음 GPU에서 틀린다.

* 아는 이름은 benchmark가 무엇이든 그 이름 그대로 쓴다(DIRECT).
* 모르는 이름은 benchmark가 유한한 양수이고 모델에 benchmark 대표값이 있을 때만
  대신 쓴다(BENCHMARK_PROXY). 아니면 예전처럼 :class:`UnknownCategoryError` 다.
* 가까움은 비율로 잰다: ``|log(benchmark) - log(대표값)|``. 처리량은 곱으로 차이 나는
  값이라 17,000과 21,000의 차이는 68,000과 72,000의 차이와 같지 않다. 거리가 같으면
  대표값이 **작은** 카테고리를 고른다(더 느리게, 즉 보수적으로 예측한다). 그래도
  같으면 이름순.

대표값은 모델 metadata의 ``gpuBenchmarkProfiles`` (학습 행의 카테고리별 중앙값,
:func:`~app.scheduler.runtime_features.gpu_benchmark_profiles`)에서 읽는다. 그 키가
생기기 전에 만든 합성 부트스트랩 모델은 같은 값을 생성기에서 복원한다
(:func:`~app.training.runtime_dataset.synthetic_benchmark_profiles`). 둘 다 없으면
대신 쓸 근거가 없으므로 예전처럼 거절한다.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import pandas as pd
from xgboost import XGBRegressor

from app.scheduler.runtime_features import (
    CATEGORICAL_FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
    RUNTIME_FEATURE_NAMES,
    CategoryMapping,
    RuntimeFeatures,
    is_usable_benchmark,
)
from app.training.runtime_dataset import (
    TRAINING_DATA_TYPE as SYNTHETIC_BOOTSTRAP,
    synthetic_benchmark_profiles,
)
from app.training.runtime_trainer import (
    METADATA_FILE_NAME,
    MODEL_FILE_NAME,
    TARGET_TRANSFORM,
)


logger = logging.getLogger(__name__)

GPU_FEATURE = "gpu_model"


class GpuCategorySource(str, Enum):
    """예측에 쓴 gpu_model 카테고리가 어디서 왔는가. 응답에 싣지 않는다."""

    DIRECT = "DIRECT"
    BENCHMARK_PROXY = "BENCHMARK_PROXY"


@dataclass(frozen=True)
class GpuCategory:
    category: str
    source: GpuCategorySource


class ModelUnavailableError(RuntimeError):
    """모델을 쓸 수 없다. 파일이 없거나, 깨졌거나, 지금 코드와 맞지 않는다."""


class IncompatibleModelError(ModelUnavailableError):
    """모델은 읽혔지만 지금 빌드와 **다른 계약**으로 학습됐다.

    "아직 학습하지 않았다"와는 다른 종류의 문제다. 앞의 것은 학습을 돌리면 풀리는
    운영 상태지만, 이것은 배포가 잘못 물려 있다는 뜻이라 기다린다고 달라지지 않고
    요청을 고쳐도 달라지지 않는다. 그래서
    :class:`~app.scheduler.runtime_model.LoadedRuntimeModel` 은 이 예외만은
    붙잡지 않고 그대로 올려 보내 앱이 시작하지 못하게 한다.
    """


class PredictionError(RuntimeError):
    """모델이 답을 냈지만 실행 시간이라고 할 수 없는 값이다."""


@dataclass(frozen=True)
class RuntimePredictor:
    """불러온 모델 하나. 요청마다 다시 만들지 않는다."""

    model: XGBRegressor
    categories: CategoryMapping
    model_version: str
    feature_schema_version: str
    training_data_type: str
    #: 학습 카테고리 → benchmark 대표값. 모르는 GPU의 proxy를 고르는 기준.
    gpu_benchmark_profiles: Mapping[str, float] = field(default_factory=dict)

    def resolve_gpu_category(self, gpu_model: str, benchmark_tokens_per_sec: object) -> GpuCategory:
        """이 행의 예측에 쓸 gpu_model 카테고리. 모듈 설명의 규칙 그대로."""
        known = self.categories.known_values(GPU_FEATURE)
        if gpu_model in known:
            return GpuCategory(gpu_model, GpuCategorySource.DIRECT)
        candidates = {
            name: value for name, value in self.gpu_benchmark_profiles.items()
            if name in known and is_usable_benchmark(value)
        }
        if not candidates or not is_usable_benchmark(benchmark_tokens_per_sec):
            # 근거가 없다. 대신 쓰지 않고 code_of가 예전처럼 거절하게 둔다.
            return GpuCategory(gpu_model, GpuCategorySource.DIRECT)
        measured = math.log(float(benchmark_tokens_per_sec))
        proxy = min(
            candidates,
            # 로그 차이를 반올림해 부동소수점 잡음이 동점을 가르지 않게 한다.
            key=lambda name: (round(abs(measured - math.log(candidates[name])), 9),
                              candidates[name], name),
        )
        return GpuCategory(proxy, GpuCategorySource.BENCHMARK_PROXY)

    def predict(self, rows: Sequence[RuntimeFeatures]) -> tuple[float, ...]:
        """후보들의 예측 시간. 순서는 넣은 순서 그대로다.

        후보마다 따로 호출하지 않고 한 번에 넣는다. XGBoost는 행렬 하나를 받는
        편이 빠르고, 무엇보다 순서가 어긋날 자리가 생기지 않는다.
        """
        if not rows:
            raise PredictionError("there are no GPU candidates to predict for")

        mappings = []
        for row in rows:
            values = row.as_mapping()
            resolved = self.resolve_gpu_category(
                row.gpu_model, row.benchmark_tokens_per_sec)
            if resolved.source is GpuCategorySource.BENCHMARK_PROXY:
                logger.info(
                    "runtime prediction gpu fallback actualModel=%r proxyCategory=%r "
                    "benchmarkTokensPerSec=%s", row.gpu_model, resolved.category,
                    row.benchmark_tokens_per_sec)
            values[GPU_FEATURE] = resolved.category
            mappings.append(values)
        frame = pd.DataFrame(mappings, columns=list(RUNTIME_FEATURE_NAMES))
        for name in CATEGORICAL_FEATURE_NAMES:
            codes = frame[name].map(lambda value: self.categories.code_of(name, value))
            known = len(self.categories.known_values(name))
            frame[name] = pd.Categorical(codes, categories=range(known))
        for name in RUNTIME_FEATURE_NAMES:
            if name not in CATEGORICAL_FEATURE_NAMES:
                frame[name] = frame[name].astype("float64")

        # 모델이 터무니없이 큰 값을 내놓으면 exp가 넘친다. 경고 대신 무한대를
        # 받아서 아래에서 걸러 낸다 -- 어차피 예측이 아니다.
        with np.errstate(over="ignore"):
            predicted = np.exp(self.model.predict(frame))
        answers = []
        for index, value in enumerate(predicted):
            seconds = float(value)
            if not math.isfinite(seconds) or seconds <= 0:
                raise PredictionError(
                    f"the model answered {seconds!r} for candidate {index}, "
                    "which is not a runtime")
            answers.append(seconds)
        return tuple(answers)


def load_runtime_predictor(models_directory: Path) -> RuntimePredictor:
    """디스크에서 모델과 그 metadata를 읽는다.

    metadata 없이 모델만 있는 경우도 실패로 다룬다. 카테고리 표가 metadata에 있어
    그것 없이는 문자열을 무엇으로 바꿔야 할지 알 수 없고, 추측하면 학습 때와 다른
    숫자를 넣게 된다.
    """
    model_path = models_directory / MODEL_FILE_NAME
    metadata_path = models_directory / METADATA_FILE_NAME

    if not model_path.is_file():
        raise ModelUnavailableError(
            f"no runtime model at {model_path}. Train one first "
            "(python -m app.training.train_runtime).")
    if not metadata_path.is_file():
        raise ModelUnavailableError(
            f"the runtime model at {model_path} has no metadata beside it "
            f"({metadata_path.name}), so its feature schema is unknown.")

    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise ModelUnavailableError(
            f"the runtime model metadata could not be read: {error}") from error
    if not isinstance(metadata, dict):
        raise ModelUnavailableError("the runtime model metadata is not an object")

    schema_version = metadata.get("featureSchemaVersion")
    if schema_version != FEATURE_SCHEMA_VERSION:
        raise IncompatibleModelError(
            "runtime model feature schema mismatch: "
            f"expected={FEATURE_SCHEMA_VERSION!r}, actual={schema_version!r}. "
            "Retrain it (python -m app.training.train_runtime).")

    names = metadata.get("featureNames")
    if list(RUNTIME_FEATURE_NAMES) != names:
        # 버전이 같은데 열이 다르면 둘 중 하나가 버전을 올리지 않고 바뀐 것이다.
        raise IncompatibleModelError(
            "the runtime model was trained on different feature columns "
            f"({names}) than this build produces ({list(RUNTIME_FEATURE_NAMES)}).")

    transform = metadata.get("targetTransform")
    if transform != TARGET_TRANSFORM:
        # log로 학습된 모델을 그대로 초로 읽으면 하루가 11초가 된다. 조용히.
        raise IncompatibleModelError(
            "runtime model target transform mismatch: "
            f"expected={TARGET_TRANSFORM!r}, actual={transform!r}. Retrain it.")

    mappings = metadata.get("categoryMappings")
    if not isinstance(mappings, dict):
        raise ModelUnavailableError(
            "the runtime model metadata has no category mappings, so string "
            "features cannot be encoded the way they were during training.")

    model = XGBRegressor()
    try:
        model.load_model(model_path)
    except Exception as error:  # xgboost raises its own types for a broken file
        raise ModelUnavailableError(
            f"the runtime model file could not be loaded: {error}") from error

    return RuntimePredictor(
        model=model,
        categories=CategoryMapping(mappings),
        model_version=str(metadata.get("modelVersion", "unknown")),
        feature_schema_version=str(schema_version),
        training_data_type=str(metadata.get("trainingDataType", "unknown")),
        gpu_benchmark_profiles=_benchmark_profiles(metadata),
    )


def _benchmark_profiles(metadata: Mapping[str, object]) -> dict[str, float]:
    """metadata의 대표값. 없으면 합성 부트스트랩 모델에 한해 생성기에서 복원한다.

    값이 이상한 항목(숫자가 아니거나 0 이하)은 버린다. 대표값이 없는 것은 모르는
    GPU를 예전처럼 거절한다는 뜻일 뿐, 모델을 못 쓴다는 뜻이 아니므로 로드는 실패시키지
    않는다.
    """
    stored = metadata.get("gpuBenchmarkProfiles")
    if isinstance(stored, dict):
        return {str(name): float(value) for name, value in stored.items()
                if is_usable_benchmark(value)}
    if metadata.get("trainingDataType") == SYNTHETIC_BOOTSTRAP:
        return synthetic_benchmark_profiles()
    return {}
