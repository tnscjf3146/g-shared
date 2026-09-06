"""검증을 통과한 Dataset Metadata를 Scheduler 내부 표현으로 옮긴다.

여기서 하는 일은 이름을 바꾸고 순서를 고정하는 것뿐이다. 스케일링도, 로그 변환도,
파생 feature 계산도 하지 않는다. 그런 것은 예측 모델이 실제로 요구할 때,
그 모델과 같은 곳에서 정해져야 한다 -- 학습 때와 추론 때 다른 전처리를 쓰는 것이
런타임 예측이 조용히 틀리는 가장 흔한 이유다.

API 스키마를 그대로 예측기에 넘기지 않는 이유는 두 가지다.

- Backend가 필드를 하나 추가하거나 이름을 바꿔도 예측기 입력은 그대로여야 한다.
- 예측기는 순서가 고정된 숫자 열을 원한다. Pydantic 모델은 그것을 보장하지 않는다.
"""

from dataclasses import dataclass

from app.schemas.dataset import DatasetMetadata

#: 예측기에 넘길 때의 열 순서. XGBoost는 학습할 때와 추론할 때의 열 순서가
#: 같아야 하므로, 순서는 이 상수 하나로만 정해진다.
FEATURE_NAMES: tuple[str, ...] = (
    "dataset_size_bytes",
    "sample_count",
    "total_tokens",
    "avg_seq_length",
    "max_seq_length",
)


@dataclass(frozen=True)
class DatasetFeatures:
    """Scheduler가 데이터셋에 대해 아는 것, 내부 이름으로.

    Pydantic이 아니라 frozen dataclass인 이유: 이 값은 이미 검증을 통과한 뒤에
    만들어지므로 다시 파싱하거나 검증할 것이 없고, 바깥으로 직렬화되지도 않는다.
    frozen이라 만들어진 뒤에는 아무도 바꿀 수 없다.
    """

    dataset_size_bytes: int
    sample_count: int
    total_tokens: int
    avg_seq_length: float
    max_seq_length: int

    def as_mapping(self) -> dict[str, float]:
        """이름 → 값. 로그와 응답에서 사람이 읽기 위한 형태."""
        return {name: float(getattr(self, name)) for name in FEATURE_NAMES}

    def as_vector(self) -> tuple[float, ...]:
        """:data:`FEATURE_NAMES` 순서대로 늘어놓은 숫자 열."""
        return tuple(float(getattr(self, name)) for name in FEATURE_NAMES)


def build_dataset_features(metadata: DatasetMetadata) -> DatasetFeatures:
    """Metadata를 내부 표현으로 옮긴다. 원본은 읽기만 한다.

    필드를 하나씩 이름으로 적어 옮기는 것은 일부러다. ``**metadata.model_dump()``
    였다면 Backend가 필드를 하나 추가한 날 그 값이 아무도 모르게 예측기 입력으로
    흘러 들어간다. 여기 적힌 것만 들어간다는 것이 이 함수의 의미다.
    """
    return DatasetFeatures(
        dataset_size_bytes=metadata.dataset_size_bytes,
        sample_count=metadata.sample_count,
        total_tokens=metadata.total_tokens,
        avg_seq_length=metadata.avg_seq_length,
        max_seq_length=metadata.max_seq_length,
    )
