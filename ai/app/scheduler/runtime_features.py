"""Runtime 예측기가 받는 한 줄. 이름과 순서가 여기서만 정해진다.

**학습할 때와 예측할 때의 열 순서가 다르면 모델은 조용히 틀린 답을 낸다.** 예외도
경고도 없이, 그럴듯한 숫자가 나온다. 그래서 순서를 정하는 곳은 이 파일 하나이고,
학습 코드도 예측 코드도 :data:`RUNTIME_FEATURE_NAMES` 를 통해서만 열을 만든다.

Dataset feature와 섞지 않은 이유
--------------------------------
:class:`~app.scheduler.features.DatasetFeatures` 는 "데이터셋이 어떻게 생겼는가"만
답한다. Runtime 한 줄은 거기에 GPU와 학습 설정이 붙어야 완성되고, 무엇보다 **GPU
후보마다 한 줄**이다 -- 같은 데이터셋으로 세 줄이 나온다. 둘을 한 클래스에 밀어
넣으면 "데이터셋 하나 = feature 하나"라는 기존 성질이 깨진다. 그래서 따로 둔다.

Feature로 넣지 않은 것
----------------------
* ``workerId`` -- 식별자다. 학습하면 관계가 아니라 이름을 외운다.
* ``learningRate`` -- 가중치를 얼마나 움직이는지는 정하지만 step 수도, step당
  계산량도 바꾸지 않는다. 실행 시간과 사실상 무관하다.
* ``datasetSizeBytes`` -- 디스크와 전송량이지 GPU 계산량이 아니다. 같은 토큰 수를
  담은 파일이 압축 여부만으로 두 배가 될 수 있다.
* ``totalTokens`` -- ``effectiveTokens`` 가 GPU에 실제로 올라가는 양을 말하므로
  실행 시간에 대응하는 쪽은 그것이다. 둘 다 넣으면 거의 같은 정보가 두 번 들어가
  트리가 어느 쪽으로 쪼갤지만 흔들린다.
* ``dockerImageId`` -- 아래 참고.

``dockerImageId`` 를 뺀 이유는 조금 다르다. 지금 G-shared는 승인된 이미지가 하나뿐이라
모든 행에서 같은 값이다. 값이 하나뿐인 열은 정보가 0이고, 트리는 그 열로 쪼갤 수
없다. 그래서 **API 계약에는 남기고 모델 feature에서는 뺐다.** 이미지가 둘 이상이
되는 날 feature schema 버전을 올려 넣으면 되고, 그 결정이 metadata에 기록된다.
지금 넣어 두면 "쓰이지 않는 열"이 하나 늘 뿐 예측이 나아지지 않는다.
"""

import math
import statistics
from dataclasses import dataclass
from typing import Mapping, Sequence

from app.schemas.runtime import GpuCandidate, RuntimeDatasetMetadata
from app.schemas.training import TrainingConfig

#: 예측기에 넘길 때의 열 순서. 학습과 예측이 공유하는 유일한 정의다.
RUNTIME_FEATURE_NAMES: tuple[str, ...] = (
    "benchmark_tokens_per_sec",
    "vram_mb",
    "sample_count",
    "effective_tokens",
    "avg_seq_length",
    "max_seq_length",
    "epochs",
    "batch_size",
    "max_length",
    "gpu_model",
    "model_id",
)

#: 문자열이라 숫자로 바꿔야 하는 열. XGBoost가 카테고리로 다루도록 표시하는 것도
#: 이 목록을 기준으로 한다.
CATEGORICAL_FEATURE_NAMES: tuple[str, ...] = ("gpu_model", "model_id")

#: Feature 구성이 바뀌면 올린다. 열 이름과 개수만이 아니라 **값의 의미**가 바뀔
#: 때도 올린다. 이 버전이 다른 모델은 지금 코드가 만드는 것과 다른 열로 학습된
#: 것이므로 불러오지 않는다.
#:
#: ``v1`` -> ``v2``: ``model_id`` 가 모델 이름 문자열(``"qwen2.5-7b"``)에서
#: Backend ``base_models`` PK 정수(``1``, ``2``, ``3``)로 바뀌었다. 열 이름도
#: 개수도 그대로라 :data:`RUNTIME_FEATURE_NAMES` 비교로는 걸러지지 않는다 --
#: 버전을 올리지 않으면 v1 artifact가 아무 말 없이 로드되고, 그 뒤로 정수
#: ``modelId`` 가 올 때마다 "본 적 없는 값"이라며 요청이 거절된다. 문제는
#: 배포에 있는데 요청이 잘못된 것처럼 보이는 셈이다.
FEATURE_SCHEMA_VERSION = "runtime-features-v2"

#: 학습이 맞히려는 값. Feature에 절대 들어가면 안 되는 이름이기도 하다.
TARGET_NAME = "actual_runtime_sec"


class UnknownCategoryError(ValueError):
    """학습 때 본 적 없는 카테고리 값이 들어왔다.

    임의의 숫자를 넣어 넘기지 않는다. 본 적 없는 GPU나 본 적 없는 기반 모델에
    대한 예측은 근거가 없고, 그럴듯한 숫자를 내놓으면 Backend는 그것이 근거
    없는 값인지 알 방법이 없다.

    GPU는 예외가 하나 있다. 이름은 처음 봐도 그 Worker가 잰 benchmark가 있으면
    benchmark가 가장 가까운 학습 카테고리로 예측한다
    (:meth:`app.scheduler.runtime_predictor.RuntimePredictor.resolve_gpu_category`).
    그 근거도 없을 때만 이 예외가 난다.
    """

    def __init__(self, feature: str, value: str, known: tuple[str, ...]) -> None:
        super().__init__(
            f"{feature}={value!r} was not seen during training "
            f"(known: {', '.join(known) if known else 'none'})"
        )
        self.feature = feature
        self.value = value
        self.known = known


@dataclass(frozen=True)
class RuntimeFeatures:
    """GPU 후보 하나에 대한 예측 입력 한 줄.

    작업 쪽 값은 후보마다 같고 GPU 쪽 값만 다르다. 그래서 후보가 셋이면 이 객체가
    셋 만들어지고, 다른 것은 앞의 네 개뿐이다.
    """

    # GPU
    benchmark_tokens_per_sec: float
    vram_mb: int
    gpu_model: str
    # Dataset
    sample_count: int
    effective_tokens: int
    avg_seq_length: float
    max_seq_length: int
    # Training
    epochs: int
    batch_size: int
    max_length: int
    # Workload
    model_id: int

    def as_mapping(self) -> dict[str, object]:
        """이름 → 값. 순서는 :data:`RUNTIME_FEATURE_NAMES` 를 따른다."""
        return {name: getattr(self, name) for name in RUNTIME_FEATURE_NAMES}

    def as_vector(self, categories: "CategoryMapping") -> tuple[float, ...]:
        """모델이 받는 숫자 열. 문자열은 ``categories`` 로 코드가 된다.

        학습 때 쓴 것과 같은 mapping을 넘겨야 한다. 그래서 mapping은 모델
        artifact 옆에 저장되고 예측할 때 거기서 읽어 온다.
        """
        row = []
        for name in RUNTIME_FEATURE_NAMES:
            value = getattr(self, name)
            if name in CATEGORICAL_FEATURE_NAMES:
                row.append(float(categories.code_of(name, value)))
            else:
                row.append(float(value))
        return tuple(row)


class CategoryMapping:
    """문자열 feature를 정수 코드로 옮기는 표. 학습 때 만들어 저장된다.

    ``hash()`` 를 쓰지 않는 이유: 파이썬의 문자열 해시는 실행마다 달라서 오늘 학습한
    모델을 내일 못 쓰게 되고, 값 사이의 크기 관계도 아무 의미가 없다. 표를 만들어
    모델과 함께 저장하면 두 문제가 같이 사라진다.

    코드는 정수지만 XGBoost에는 카테고리 열로 알려 주므로 "4070 < 4090" 같은
    순서로 해석되지 않는다. 트리는 값의 집합을 나누는 방식으로 쪼갠다.

    ``model_id`` 처럼 값 자체가 이미 정수(DB PK)인 열도 같은 길을 지난다. PK의
    크기에는 아무 의미가 없기 때문이다 -- 1번 모델과 2번 모델 사이에 "2가 더
    크다"고 할 만한 것이 없다. 그래서 숫자를 그대로 트리에 넘기지 않고 표를 거쳐
    범주로 만든다. ``str(value)`` 로 조회하므로 문자열이든 정수든 같은 코드를 지난다.
    """

    def __init__(self, tables: Mapping[str, Mapping[str, int]]) -> None:
        self._tables = {
            feature: dict(table) for feature, table in tables.items()
        }

    @classmethod
    def learned_from(cls, rows: list[Mapping[str, object]]) -> "CategoryMapping":
        """학습 데이터에 나온 값들로 표를 만든다.

        정렬해서 번호를 매기는 것은 재현성 때문이다. 같은 데이터면 몇 번을 돌려도
        같은 표가 나와야 어제 학습한 모델과 오늘 학습한 모델을 비교할 수 있다.
        """
        tables: dict[str, dict[str, int]] = {}
        for feature in CATEGORICAL_FEATURE_NAMES:
            seen = sorted({str(row[feature]) for row in rows})
            tables[feature] = {value: code for code, value in enumerate(seen)}
        return cls(tables)

    def code_of(self, feature: str, value: object) -> int:
        table = self._tables.get(feature, {})
        text = str(value)
        if text not in table:
            raise UnknownCategoryError(feature, text, tuple(table))
        return table[text]

    def known_values(self, feature: str) -> tuple[str, ...]:
        return tuple(self._tables.get(feature, {}))

    def as_payload(self) -> dict[str, dict[str, int]]:
        """metadata에 저장할 모양."""
        return {feature: dict(table) for feature, table in self._tables.items()}


def is_usable_benchmark(value: object) -> bool:
    """proxy 선택에 쓸 수 있는 benchmark인가. 유한한 양수만. bool은 숫자가 아니다."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


def gpu_benchmark_profiles(rows: Sequence[Mapping[str, object]]) -> dict[str, float]:
    """``gpu_model`` 카테고리마다 학습 행의 ``benchmark_tokens_per_sec`` 중앙값.

    학습 때 모델이 본 "이 이름의 GPU는 대략 이만큼 빠르다"를 숫자 하나로 남긴다.
    본 적 없는 GPU를 예측할 때 그 GPU의 실측 benchmark와 가장 가까운 카테고리를
    고르는 기준이다(:mod:`app.scheduler.runtime_predictor`). 평균이 아니라 중앙값인
    것은, 실측 이력이 쌓이면 한 번 튄 측정이 대표값을 끌고 가지 않게 하기 위해서다.
    쓸 수 없는 benchmark(0 이하, NaN, 무한대)는 세지 않는다.
    """
    values: dict[str, list[float]] = {}
    for row in rows:
        benchmark = row.get("benchmark_tokens_per_sec")
        if is_usable_benchmark(benchmark):
            values.setdefault(str(row["gpu_model"]), []).append(float(benchmark))
    return {name: float(statistics.median(found)) for name, found in sorted(values.items())}


def build_runtime_features(
    *,
    dataset: RuntimeDatasetMetadata,
    training: TrainingConfig,
    model_id: int,
    candidate: GpuCandidate,
) -> RuntimeFeatures:
    """작업 하나와 GPU 하나를 합쳐 한 줄로 만든다.

    필드를 하나씩 이름으로 옮겨 적는 것은 일부러다. ``**model_dump()`` 였다면
    Backend가 필드를 추가한 날 그 값이 아무도 모르게 예측기 입력이 되고, 학습된
    모델의 열 구성과 어긋난다. 여기 적힌 것만 들어간다.
    """
    return RuntimeFeatures(
        benchmark_tokens_per_sec=candidate.benchmark_tokens_per_sec,
        vram_mb=candidate.vram_mb,
        gpu_model=candidate.gpu_model,
        sample_count=dataset.sample_count,
        effective_tokens=dataset.effective_tokens,
        avg_seq_length=dataset.avg_seq_length,
        max_seq_length=dataset.max_seq_length,
        epochs=training.epochs,
        batch_size=training.batch_size,
        max_length=training.max_length,
        model_id=model_id,
    )
