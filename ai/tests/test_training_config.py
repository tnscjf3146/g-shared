"""Training Config 스키마와 검증.

네 값 모두 0 이하가 의미를 갖지 못한다는 것이 이 파일의 대부분이다. 상한이
없다는 것도 함께 고정한다 -- 상한은 이 계층이 아니라 실행 가능성 검증의 몫이다.
"""

import math

import pytest
from pydantic import ValidationError

from app.schemas.training import TrainingConfig

VALID = {
    "epochs": 3,
    "batchSize": 8,
    "learningRate": 0.00002,
    "maxLength": 512,
}

REQUIRED_FIELDS = list(VALID)


def config(**overrides):
    return TrainingConfig.model_validate({**VALID, **overrides})


def errors_for(payload):
    with pytest.raises(ValidationError) as refused:
        TrainingConfig.model_validate(payload)
    return refused.value.errors()


class TestNormalInput:
    def test_every_field_is_read(self):
        parsed = config()

        assert parsed.epochs == 3
        assert parsed.batch_size == 8
        assert parsed.learning_rate == 0.00002
        assert parsed.max_length == 512

    def test_the_wire_format_is_camel_case(self):
        assert set(config().model_dump(by_alias=True)) == set(VALID)

    def test_a_whole_number_learning_rate_is_accepted_as_a_float(self):
        assert config(learningRate=1).learning_rate == 1.0

    def test_a_validated_config_cannot_be_changed_afterwards(self):
        with pytest.raises(ValidationError):
            config().epochs = 10


class TestEpochs:
    def test_zero_epochs_is_not_training(self):
        assert errors_for({**VALID, "epochs": 0})[0]["loc"] == ("epochs",)

    def test_a_negative_number_of_epochs_is_refused(self):
        assert errors_for({**VALID, "epochs": -1})[0]["loc"] == ("epochs",)

    def test_half_an_epoch_is_not_a_whole_number_of_passes(self):
        assert errors_for({**VALID, "epochs": 1.5})[0]["loc"] == ("epochs",)

    def test_it_is_required(self):
        payload = {k: v for k, v in VALID.items() if k != "epochs"}

        assert errors_for(payload)[0]["type"] == "missing"

    def test_one_epoch_is_the_smallest_real_run(self):
        assert config(epochs=1).epochs == 1


class TestBatchSize:
    def test_a_batch_of_zero_samples_is_refused(self):
        assert errors_for({**VALID, "batchSize": 0})[0]["loc"] == ("batchSize",)

    def test_a_negative_batch_size_is_refused(self):
        assert errors_for({**VALID, "batchSize": -8})[0]["loc"] == ("batchSize",)

    def test_a_fractional_batch_size_is_refused(self):
        assert errors_for({**VALID, "batchSize": 8.5})[0]["loc"] == ("batchSize",)

    def test_it_is_required(self):
        payload = {k: v for k, v in VALID.items() if k != "batchSize"}

        assert errors_for(payload)[0]["type"] == "missing"

    def test_a_batch_of_one_is_allowed(self):
        assert config(batchSize=1).batch_size == 1


class TestLearningRate:
    def test_zero_means_the_weights_never_move(self):
        assert errors_for({**VALID, "learningRate": 0})[0]["loc"] == ("learningRate",)

    def test_a_negative_rate_would_climb_the_loss(self):
        assert errors_for({**VALID, "learningRate": -0.001})[0]["loc"] == ("learningRate",)

    def test_text_is_not_a_rate(self):
        assert errors_for({**VALID, "learningRate": "2e-5"})[0]["loc"] == ("learningRate",)

    def test_it_is_required(self):
        payload = {k: v for k, v in VALID.items() if k != "learningRate"}

        assert errors_for(payload)[0]["type"] == "missing"

    def test_the_very_small_values_this_field_normally_holds_are_fine(self):
        # 1e-5 규모가 이 필드의 보통 값이다. 하한을 gt=0 말고 더 두면 정상값이 막힌다.
        assert config(learningRate=1e-6).learning_rate == 1e-6

    def test_an_ordinary_rate_is_accepted(self):
        assert config(learningRate=0.0002).learning_rate == 0.0002

    def test_infinity_is_not_a_rate(self):
        # gt=0 만으로는 통과한다 -- inf > 0 은 참이다. 명시적으로 막혀 있어야 한다.
        assert errors_for(
            {**VALID, "learningRate": math.inf})[0]["loc"] == ("learningRate",)

    def test_negative_infinity_is_not_a_rate(self):
        assert errors_for(
            {**VALID, "learningRate": -math.inf})[0]["loc"] == ("learningRate",)

    def test_nan_is_not_a_rate(self):
        assert errors_for(
            {**VALID, "learningRate": math.nan})[0]["loc"] == ("learningRate",)


class TestMaxLength:
    def test_a_length_of_zero_leaves_no_tokens_to_learn_from(self):
        assert errors_for({**VALID, "maxLength": 0})[0]["loc"] == ("maxLength",)

    def test_a_negative_length_is_refused(self):
        assert errors_for({**VALID, "maxLength": -512})[0]["loc"] == ("maxLength",)

    def test_a_fractional_length_is_refused(self):
        assert errors_for({**VALID, "maxLength": 512.5})[0]["loc"] == ("maxLength",)

    def test_it_is_required(self):
        payload = {k: v for k, v in VALID.items() if k != "maxLength"}

        assert errors_for(payload)[0]["type"] == "missing"

    def test_no_upper_limit_is_imposed(self):
        # 어떤 모델은 128k 문맥을 받는다. 여기서 상한을 두면 그런 요청이 막힌다.
        assert config(maxLength=131072).max_length == 131072


class TestTheSameShapeForAutoAndCustom:
    def test_nothing_records_who_produced_this_config(self):
        # AUTO와 CUSTOM은 이 값을 만드는 방법이 다를 뿐 결과는 같은 모양이다.
        # mode 같은 필드가 생기면 이후 모든 소비자가 다시 분기하게 된다.
        assert set(TrainingConfig.model_fields) == {
            "epochs", "batch_size", "learning_rate", "max_length"
        }

    def test_every_field_is_required_so_neither_side_may_leave_one_out(self):
        # AUTO가 셋만 계산하고 하나를 비워 두면 그 뒤가 값을 채워야 한다.
        # 네 값이 모두 정해진 것만 Training Config다.
        assert len(errors_for({})) == len(REQUIRED_FIELDS)


class TestUnknownFields:
    def test_a_field_nobody_defined_is_refused(self):
        assert errors_for({**VALID, "warmupSteps": 100})[0]["type"] == "extra_forbidden"
