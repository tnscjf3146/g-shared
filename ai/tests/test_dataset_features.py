"""전처리: Metadata → Scheduler 내부 표현.

이 계층이 하는 일은 이름을 바꾸고 순서를 고정하는 것뿐이다. 그래서 테스트도
"값이 그대로 옮겨졌는가", "원본이 그대로인가", "순서가 고정인가" 세 가지를 본다.
"""

import dataclasses

import pytest

from app.schemas.dataset import DatasetMetadata
from app.scheduler.features import (
    FEATURE_NAMES,
    DatasetFeatures,
    build_dataset_features,
)

VALID = {
    "datasetSizeBytes": 1048576,
    "sampleCount": 1000,
    "totalTokens": 256000,
    "avgSeqLength": 256.0,
    "maxSeqLength": 512,
}


@pytest.fixture
def metadata():
    return DatasetMetadata.model_validate(VALID)


class TestFieldMapping:
    def test_every_value_arrives_unchanged(self, metadata):
        features = build_dataset_features(metadata)

        assert features.dataset_size_bytes == 1048576
        assert features.sample_count == 1000
        assert features.total_tokens == 256000
        assert features.avg_seq_length == 256.0
        assert features.max_seq_length == 512

    def test_each_api_field_maps_to_the_internal_name_of_the_same_meaning(self, metadata):
        # camelCase(계약) ↔ snake_case(내부)가 어긋나면 예측기가 엉뚱한 열을 읽는다.
        features = build_dataset_features(metadata)
        pairs = {
            "datasetSizeBytes": "dataset_size_bytes",
            "sampleCount": "sample_count",
            "totalTokens": "total_tokens",
            "avgSeqLength": "avg_seq_length",
            "maxSeqLength": "max_seq_length",
        }

        for api_name, internal_name in pairs.items():
            assert getattr(features, internal_name) == VALID[api_name]

    def test_the_internal_representation_has_exactly_these_five_fields(self):
        # 여기 없는 필드가 예측기 입력으로 흘러 들어가지 않는다는 뜻이다.
        assert {f.name for f in dataclasses.fields(DatasetFeatures)} == set(FEATURE_NAMES)

    def test_nothing_is_scaled_or_transformed(self, metadata):
        # 로그 변환이나 정규화는 예측 모델이 요구할 때 그 모델과 같은 곳에서
        # 정해져야 한다. 학습 때와 추론 때 전처리가 달라지면 예측이 조용히 틀린다.
        features = build_dataset_features(metadata)

        assert features.as_vector() == (1048576.0, 1000.0, 256000.0, 256.0, 512.0)


class TestTheOriginalIsNotTouched:
    def test_the_metadata_is_the_same_after_preprocessing(self, metadata):
        before = metadata.model_dump()

        build_dataset_features(metadata)

        assert metadata.model_dump() == before

    def test_the_features_are_a_separate_object(self, metadata):
        features = build_dataset_features(metadata)

        assert features is not metadata

    def test_the_features_cannot_be_changed_once_built(self, metadata):
        features = build_dataset_features(metadata)

        with pytest.raises(dataclasses.FrozenInstanceError):
            features.sample_count = 1


class TestAStableOrderForThePredictor:
    def test_the_vector_follows_the_declared_order(self, metadata):
        features = build_dataset_features(metadata)
        mapping = features.as_mapping()

        assert features.as_vector() == tuple(mapping[name] for name in FEATURE_NAMES)

    def test_the_mapping_uses_the_internal_names(self, metadata):
        assert set(build_dataset_features(metadata).as_mapping()) == set(FEATURE_NAMES)

    def test_everything_the_predictor_sees_is_a_number(self, metadata):
        # XGBoost는 숫자 열을 받는다. int와 float가 섞여 있으면 열마다 타입이
        # 달라지므로 모두 float로 맞춘다.
        values = build_dataset_features(metadata).as_mapping().values()

        assert all(isinstance(value, float) for value in values)

    def test_two_identical_datasets_produce_identical_features(self, metadata):
        other = DatasetMetadata.model_validate(VALID)

        assert build_dataset_features(metadata) == build_dataset_features(other)
