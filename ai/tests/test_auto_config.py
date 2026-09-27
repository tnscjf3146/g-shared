"""AUTO: Mode + Dataset → TrainingConfig.

세 Mode가 서로 어떻게 다른지, 데이터셋이 어디에 영향을 주는지, 그리고
"데이터셋이 가진 것보다 더 요구하지 않는다"는 규칙이 지켜지는지를 고정한다.
"""

import pytest

from app.schemas.dataset import DatasetMetadata
from app.schemas.training import TrainingConfig, TrainingMode
from app.scheduler.auto_config import (
    MODE_RULES,
    TOKENS_PER_STEP,
    build_auto_config,
)

#: 세 Mode의 상한(2048)보다 긴 데이터셋. 상한 쪽이 이기는 경우를 보기 위한 것.
LONG = {
    "datasetSizeBytes": 1048576,
    "sampleCount": 1000,
    "totalTokens": 256000,
    "avgSeqLength": 256.0,
    "maxSeqLength": 4096,
}


def dataset(**overrides):
    return DatasetMetadata.model_validate({**LONG, **overrides})


class TestEveryModeProducesAConfig:
    @pytest.mark.parametrize("mode", list(TrainingMode))
    def test_it_returns_a_training_config(self, mode):
        assert isinstance(build_auto_config(mode, dataset()), TrainingConfig)

    @pytest.mark.parametrize("mode", list(TrainingMode))
    def test_the_result_satisfies_the_shared_schema(self, mode):
        # AUTO가 만든 것과 CUSTOM이 보낸 것은 같은 모양이어야 한다.
        # 다시 검증해도 통과한다는 것이 그 확인이다.
        config = build_auto_config(mode, dataset())

        assert TrainingConfig.model_validate(config.model_dump()) == config

    @pytest.mark.parametrize("mode", list(TrainingMode))
    def test_nothing_records_which_mode_made_it(self, mode):
        # Mode는 설정을 만들 때의 요청 의미이지 설정의 일부가 아니다.
        assert "mode" not in build_auto_config(mode, dataset()).model_dump()

    def test_there_is_a_rule_for_every_mode_and_no_others(self):
        assert set(MODE_RULES) == set(TrainingMode)


class TestSpeed:
    """빨리 끝내는 것이 먼저."""

    @pytest.fixture
    def config(self):
        return build_auto_config(TrainingMode.SPEED, dataset())

    def test_it_passes_over_the_data_once(self, config):
        assert config.epochs == 1

    def test_it_cuts_sequences_shortest(self, config):
        assert config.max_length == 512

    def test_it_uses_the_largest_batch(self, config):
        assert config.batch_size == 16

    def test_it_is_the_fastest_of_the_three(self):
        # epoch 수와 시퀀스 길이 둘 다 가장 작다. 이 둘이 실행 시간을 좌우한다.
        speed = build_auto_config(TrainingMode.SPEED, dataset())
        others = [
            build_auto_config(mode, dataset())
            for mode in (TrainingMode.BALANCED, TrainingMode.TRAINING)
        ]

        assert all(speed.epochs < other.epochs for other in others)
        assert all(speed.max_length < other.max_length for other in others)


class TestBalanced:
    """특별한 이유가 없을 때의 선택."""

    @pytest.fixture
    def config(self):
        return build_auto_config(TrainingMode.BALANCED, dataset())

    def test_it_uses_the_common_sft_default_of_three_epochs(self, config):
        assert config.epochs == 3

    def test_its_values_sit_between_the_other_two(self, config):
        speed = build_auto_config(TrainingMode.SPEED, dataset())
        training = build_auto_config(TrainingMode.TRAINING, dataset())

        assert speed.epochs < config.epochs < training.epochs
        assert speed.max_length < config.max_length < training.max_length
        assert training.batch_size < config.batch_size < speed.batch_size


class TestTraining:
    """잘 배우는 것이 먼저."""

    @pytest.fixture
    def config(self):
        return build_auto_config(TrainingMode.TRAINING, dataset())

    def test_it_passes_over_the_data_most_often(self, config):
        assert config.epochs == 5

    def test_it_keeps_the_longest_sequences(self, config):
        assert config.max_length == 2048

    def test_it_uses_the_smallest_batch(self, config):
        # 표가 batch × length를 한 값으로 묶어 두었기 때문. 길이를 늘린 만큼 배치가 준다.
        assert config.batch_size == 4


class TestTheTokenBudgetTiesTheThreeModesTogether:
    """batch × length를 같은 값으로 둔 것은 표를 한 축으로 설명하기 위한
    초기 heuristic이다. **VRAM 사용량이 같다는 뜻이 아니다** -- 실제 메모리는
    모델 크기, activation, attention 구현, optimizer 등에도 좌우된다.
    실행 가능성은 이후 GPU 검증 단계가 판단한다.
    """

    @pytest.mark.parametrize("mode", list(TrainingMode))
    def test_batch_times_length_is_the_same_in_every_row(self, mode):
        rule = MODE_RULES[mode]

        assert rule.batch_size * rule.max_length_cap == TOKENS_PER_STEP

    def test_length_and_batch_move_in_opposite_directions(self):
        # 이 표가 말하는 것은 여기까지다: 길게 가려면 배치를 줄이고, 배치를 키우려면
        # 짧게 간다. 어느 쪽이 어느 GPU에서 돌아가는지는 말하지 않는다.
        ordered = [MODE_RULES[mode] for mode in
                   (TrainingMode.SPEED, TrainingMode.BALANCED, TrainingMode.TRAINING)]
        lengths = [rule.max_length_cap for rule in ordered]
        batches = [rule.batch_size for rule in ordered]

        assert lengths == sorted(lengths)
        assert batches == sorted(batches, reverse=True)


class TestTheLearningRateDoesNotDependOnTheMode:
    def test_all_three_use_the_same_rate(self):
        rates = {rule.learning_rate for rule in MODE_RULES.values()}

        assert len(rates) == 1

    def test_it_is_the_qlora_sft_default(self):
        assert MODE_RULES[TrainingMode.BALANCED].learning_rate == 2e-4


class TestAutoNeverPicksMoreThanTheDataHas:
    """AUTO가 데이터셋에서 읽는 값은 두 개뿐이고, 둘 다 같은 이유로 쓴다.

    이것은 AUTO가 스스로 고르는 값에 대한 규칙이지, 넘는 값이 실행 불가능하다는
    주장이 아니다. CUSTOM으로 직접 넣는 것은 막지 않는다.
    """

    def test_a_short_dataset_shortens_max_length(self):
        # 데이터에 그만큼 긴 샘플이 없는데 AUTO가 스스로 더 잡을 이유가 없다.
        # (더 잡는 것이 실행 불가능하다는 뜻은 아니다 -- CUSTOM은 막지 않는다.)
        config = build_auto_config(
            TrainingMode.TRAINING, dataset(maxSeqLength=128, avgSeqLength=80.0)
        )

        assert config.max_length == 128

    def test_a_long_dataset_is_cut_at_the_mode_cap(self):
        config = build_auto_config(TrainingMode.SPEED, dataset(maxSeqLength=4096))

        assert config.max_length == 512

    def test_a_dataset_exactly_at_the_cap_keeps_the_cap(self):
        config = build_auto_config(TrainingMode.BALANCED, dataset(maxSeqLength=1024))

        assert config.max_length == 1024

    def test_a_small_dataset_shrinks_the_batch(self):
        # 샘플 수보다 큰 배치를 AUTO가 먼저 고르지는 않는다. DataLoader가 더 작은
        # 배치 하나로 돌릴 수도 있지만, 그것은 AUTO가 의도한 설정이 아니다.
        config = build_auto_config(TrainingMode.SPEED, dataset(sampleCount=10))

        assert config.batch_size == 10

    def test_a_dataset_exactly_the_size_of_the_batch_keeps_the_batch(self):
        config = build_auto_config(TrainingMode.SPEED, dataset(sampleCount=16))

        assert config.batch_size == 16

    def test_a_single_sample_dataset_still_produces_a_valid_config(self):
        config = build_auto_config(
            TrainingMode.TRAINING,
            dataset(sampleCount=1, maxSeqLength=1, avgSeqLength=1.0, totalTokens=1),
        )

        assert config.batch_size == 1
        assert config.max_length == 1

    def test_the_other_three_metadata_values_do_not_change_anything(self):
        # dataset_size_bytes / total_tokens / avg_seq_length 는 AUTO가 쓰지 않는다.
        # 실행 시간을 예측하는 데 쓰이는 값이지 설정을 정하는 데 쓰이지 않는다.
        base = build_auto_config(TrainingMode.BALANCED, dataset())
        different = build_auto_config(
            TrainingMode.BALANCED,
            dataset(datasetSizeBytes=9, totalTokens=7, avgSeqLength=3.0),
        )

        assert base == different


class TestTheInputIsNotTouched:
    @pytest.mark.parametrize("mode", list(TrainingMode))
    def test_the_metadata_is_the_same_afterwards(self, mode):
        metadata = dataset()
        before = metadata.model_dump()

        build_auto_config(mode, metadata)

        assert metadata.model_dump() == before
