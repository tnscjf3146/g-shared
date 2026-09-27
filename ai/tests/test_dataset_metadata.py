"""Dataset Metadata 스키마와 검증.

여기서 막지 못한 값은 그대로 Runtime Predictor의 입력이 된다. 그래서 "통과해야 하는
것이 통과하는가"만큼 "막아야 하는 것이 막히는가"를 같은 무게로 확인한다.
"""

import pytest
from pydantic import ValidationError

from app.schemas.dataset import DatasetMetadata

VALID = {
    "datasetSizeBytes": 1048576,
    "sampleCount": 1000,
    "totalTokens": 256000,
    "avgSeqLength": 256.0,
    "maxSeqLength": 512,
}

REQUIRED_FIELDS = list(VALID)


def metadata(**overrides):
    return DatasetMetadata.model_validate({**VALID, **overrides})


def errors_for(payload):
    with pytest.raises(ValidationError) as refused:
        DatasetMetadata.model_validate(payload)
    return refused.value.errors()


class TestNormalInput:
    def test_every_field_is_read(self):
        parsed = metadata()

        assert parsed.dataset_size_bytes == 1048576
        assert parsed.sample_count == 1000
        assert parsed.total_tokens == 256000
        assert parsed.avg_seq_length == 256.0
        assert parsed.max_seq_length == 512

    def test_the_wire_format_is_camel_case(self):
        # Backend(Spring)가 보내는 이름 그대로여야 한다.
        assert set(metadata().model_dump(by_alias=True)) == set(VALID)

    def test_python_code_may_use_the_snake_case_names(self):
        # 테스트와 내부 호출이 JSON 이름을 흉내 내지 않아도 되게 한다.
        parsed = DatasetMetadata(
            dataset_size_bytes=1,
            sample_count=1,
            total_tokens=1,
            avg_seq_length=1.0,
            max_seq_length=1,
        )

        assert parsed.sample_count == 1

    def test_an_average_that_is_not_a_whole_number_is_fine(self):
        # 평균이므로 정수일 이유가 없다.
        assert metadata(avgSeqLength=127.5).avg_seq_length == 127.5

    def test_an_integer_average_is_accepted_as_a_float(self):
        assert metadata(avgSeqLength=256).avg_seq_length == 256.0

    def test_a_validated_request_cannot_be_changed_afterwards(self):
        # 검증을 통과한 값이 뒤에서 바뀌면 검증한 의미가 없다.
        with pytest.raises(ValidationError):
            metadata().sample_count = 2


class TestMissingFields:
    @pytest.mark.parametrize("field", REQUIRED_FIELDS)
    def test_every_field_is_required(self, field):
        payload = {k: v for k, v in VALID.items() if k != field}

        assert errors_for(payload)[0]["type"] == "missing"

    def test_the_error_names_the_field_that_was_missing(self):
        payload = {k: v for k, v in VALID.items() if k != "sampleCount"}

        assert errors_for(payload)[0]["loc"] == ("sampleCount",)

    def test_an_empty_body_reports_all_of_them_at_once(self):
        # 한 번에 하나씩 고치게 하지 않는다.
        assert len(errors_for({})) == len(REQUIRED_FIELDS)


class TestWrongTypes:
    def test_text_is_not_a_count(self):
        assert errors_for({**VALID, "sampleCount": "many"})[0]["loc"] == ("sampleCount",)

    def test_null_is_not_a_number(self):
        assert errors_for({**VALID, "totalTokens": None})

    def test_a_fractional_sample_count_is_refused(self):
        # 샘플 1000.5개는 있을 수 없다. 조용히 버림하면 값이 바뀐 줄 모른다.
        assert errors_for({**VALID, "sampleCount": 1000.5})

    def test_a_boolean_is_not_a_count(self):
        assert errors_for({**VALID, "maxSeqLength": True})

    def test_a_numeric_string_is_still_not_a_number(self):
        # JSON에는 숫자 타입이 있다. 문자열로 왔다면 보내는 쪽이 잘못 만든 것이다.
        assert errors_for({**VALID, "sampleCount": "1000"})


class TestBoundaryValues:
    @pytest.mark.parametrize("field", REQUIRED_FIELDS)
    def test_zero_is_refused_for_every_field(self, field):
        # 다섯 값 중 어느 하나라도 0이면 학습할 것이 없는 데이터셋이다.
        assert errors_for({**VALID, field: 0})[0]["loc"] == (field,)

    @pytest.mark.parametrize("field", REQUIRED_FIELDS)
    def test_a_negative_value_is_refused_for_every_field(self, field):
        assert errors_for({**VALID, field: -1})[0]["loc"] == (field,)

    def test_one_is_the_smallest_thing_that_can_be_learned_from(self):
        parsed = metadata(
            datasetSizeBytes=1, sampleCount=1, totalTokens=1,
            avgSeqLength=1.0, maxSeqLength=1,
        )

        assert parsed.sample_count == 1

    def test_a_very_small_average_length_is_allowed(self):
        assert metadata(avgSeqLength=0.5).avg_seq_length == 0.5

    def test_no_upper_limit_is_imposed(self):
        # "이 정도면 너무 크다"는 판단은 GPU 선택과 실행 가능성 검증의 몫이다.
        # 여기서 근거 없는 상한을 두면 멀쩡한 요청이 막힌다.
        huge = metadata(
            datasetSizeBytes=10**15, sampleCount=10**9,
            totalTokens=10**12, avgSeqLength=1000.0, maxSeqLength=1_000_000,
        )

        assert huge.total_tokens == 10**12


class TestFieldsThatMustAgree:
    def test_an_average_longer_than_the_maximum_is_refused(self):
        # 정의상 있을 수 없는 값이다. 통과시키면 예측기가 앞뒤가 맞지 않는
        # 근거로 시간을 추정하게 된다.
        assert errors_for({**VALID, "avgSeqLength": 600.0, "maxSeqLength": 512})

    def test_an_average_equal_to_the_maximum_is_allowed(self):
        # 모든 샘플의 길이가 같은 데이터셋.
        assert metadata(avgSeqLength=512.0, maxSeqLength=512).avg_seq_length == 512.0

    def test_the_message_says_which_two_values_disagree(self):
        message = errors_for({**VALID, "avgSeqLength": 600.0})[0]["msg"]

        assert "avgSeqLength" in message
        assert "maxSeqLength" in message


class TestUnknownFields:
    def test_a_field_nobody_defined_is_refused(self):
        # 조용히 무시하면 Backend가 오타 난 필드를 계속 보내면서
        # 값이 반영됐다고 믿게 된다.
        assert errors_for({**VALID, "datasetSizeBytes2": 1})[0]["type"] == "extra_forbidden"

    def test_a_misspelled_field_is_reported_as_both_extra_and_missing(self):
        payload = {k: v for k, v in VALID.items() if k != "sampleCount"}
        payload["samplecount"] = 1000

        kinds = {error["type"] for error in errors_for(payload)}

        assert kinds == {"extra_forbidden", "missing"}
