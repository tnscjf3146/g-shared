"""CUSTOM: 사용자 설정이 이 데이터셋에 대해 말이 되는가.

지금 이 계층에는 규칙이 하나도 없다. 그래서 이 파일이 고정하는 것은 두 가지다.

1. **무엇을 거절하지 않는가** -- Schema를 통과한 설정은 여기서 더 막히지 않는다.
   근거 없이 거절하는 것은 근거 없이 고치는 것과 같은 잘못이다.
2. **고치지 않는다** -- 통과하든 아니든 사용자 값도 데이터셋도 그대로 남는다.

규칙을 붙일 자리와 응답 모양은 이미 놓여 있으므로, 그 배선도 함께 확인한다.
"""

import dataclasses

import pytest

from app.schemas.dataset import DatasetMetadata
from app.schemas.training import TrainingConfig
from app.scheduler.policy import PolicyReport, PolicyViolation, check_training_policy

DATASET = {
    "datasetSizeBytes": 1048576,
    "sampleCount": 1000,
    "totalTokens": 256000,
    "avgSeqLength": 256.0,
    "maxSeqLength": 512,
}
CONFIG = {
    "epochs": 3,
    "batchSize": 8,
    "learningRate": 0.0002,
    "maxLength": 512,
}
SMALL = {
    "datasetSizeBytes": 4096,
    "sampleCount": 10,
    "totalTokens": 800,
    "avgSeqLength": 80.0,
    "maxSeqLength": 128,
}


def dataset(**overrides):
    return DatasetMetadata.model_validate({**DATASET, **overrides})


def config(**overrides):
    return TrainingConfig.model_validate({**CONFIG, **overrides})


def check(config_overrides=None, dataset_overrides=None) -> PolicyReport:
    return check_training_policy(
        config(**(config_overrides or {})), dataset(**(dataset_overrides or {}))
    )


class TestAConfigThatFits:
    def test_it_is_accepted(self):
        assert check().is_acceptable

    def test_nothing_is_reported(self):
        assert check().violations == ()

    def test_a_max_length_shorter_than_the_data_is_fine(self):
        assert check({"maxLength": 128}).is_acceptable

    def test_a_batch_smaller_than_the_dataset_is_fine(self):
        assert check({"batchSize": 4}).is_acceptable


class TestNothingIsRefusedWithoutEvidence:
    """Schema를 통과한 설정은 이 단계에서 더 막히지 않는다.

    아래 두 조건은 한때 거절 사유였다가 빠졌다. 승인 Docker Image의
    preprocessing / DataLoader 계약이 정해지지 않아, 실행이 실패한다고
    단정할 수 없기 때문이다.
    """

    def test_a_max_length_longer_than_the_longest_sample_is_allowed(self):
        # maxLength가 truncation 상한이고 dynamic padding을 쓴다면 아무 문제 없이
        # 돌아간다. 낭비인지 아닌지는 이미지가 어떻게 padding 하느냐에 달렸다.
        report = check_training_policy(
            config(maxLength=2048), DatasetMetadata.model_validate(SMALL)
        )

        assert report.is_acceptable

    def test_a_batch_larger_than_the_dataset_is_allowed(self):
        # 일반적인 DataLoader는 남은 샘플로 더 작은 배치 하나를 만들어 그대로 돈다.
        # drop_last=True 라면 실패하겠지만 그 설정이 무엇인지 정해진 바가 없다.
        report = check_training_policy(
            config(batchSize=32), DatasetMetadata.model_validate(SMALL)
        )

        assert report.is_acceptable

    def test_both_at_once_are_still_allowed(self):
        report = check_training_policy(
            config(maxLength=2048, batchSize=32),
            DatasetMetadata.model_validate(SMALL),
        )

        assert report.is_acceptable

    @pytest.mark.parametrize(
        "overrides",
        [
            {"epochs": 100},
            {"batchSize": 4096},
            {"learningRate": 0.5},
            {"maxLength": 131072},
        ],
    )
    def test_an_unusual_but_well_formed_config_is_not_second_guessed(self, overrides):
        # "이상해 보인다"는 거절 사유가 아니다. 실제로 돌아가는지는 GPU 검증이,
        # 잘 배우는지는 사용자가 판단한다.
        assert check(overrides).is_acceptable


class TestNothingIsCorrected:
    """이 파일에서 가장 중요한 부분."""

    def test_the_users_config_is_unchanged(self):
        user_config = config(maxLength=2048, batchSize=32)
        before = user_config.model_dump()

        check_training_policy(user_config, dataset())

        assert user_config.model_dump() == before

    def test_the_dataset_is_unchanged_too(self):
        metadata = dataset()
        before = metadata.model_dump()

        check_training_policy(config(maxLength=2048), metadata)

        assert metadata.model_dump() == before

    def test_the_result_is_a_report_and_not_a_config(self):
        # 고쳐진 설정을 돌려주는 순간 호출하는 쪽이 그것을 쓰게 된다.
        result = check_training_policy(config(maxLength=2048), dataset())

        assert isinstance(result, PolicyReport)
        assert not hasattr(result, "max_length")


class TestWhatThisLayerDeliberatelyDoesNotJudge:
    def test_it_says_nothing_about_whether_a_gpu_can_run_this(self):
        # VRAM 판단은 GPU 후보를 알아야 답할 수 있다. 같은 판단을 두 계층에서
        # 하면 언젠가 두 답이 갈린다.
        report = check({"batchSize": 512, "maxLength": 512}, {"sampleCount": 100000})

        assert report.is_acceptable

    def test_it_does_not_repeat_what_the_schema_already_checks(self):
        # epochs > 0 같은 것은 TrainingConfig가 이미 막으므로 여기까지 오지 않는다.
        # 그런 값으로는 TrainingConfig를 만들 수조차 없다.
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            config(epochs=0)


class TestTheSeamIsReadyForRulesThatArriveLater:
    """규칙이 생겼을 때 쓰일 자료구조와 응답 모양. 규칙 없이도 확인할 수 있다."""

    def test_a_report_with_violations_is_not_acceptable(self):
        report = PolicyReport(
            (PolicyViolation("SOME_CODE", "maxLength", "because of something"),)
        )

        assert not report.is_acceptable

    def test_an_empty_report_is_acceptable(self):
        assert PolicyReport(()).is_acceptable

    def test_a_violation_cannot_be_edited_afterwards(self):
        violation = PolicyViolation("SOME_CODE", "maxLength", "because")

        with pytest.raises(dataclasses.FrozenInstanceError):
            violation.code = "SOMETHING_ELSE"

    def test_violations_become_the_standard_error_shape(self):
        from app.api.scheduler import _as_error_detail

        detail = _as_error_detail(
            PolicyReport(
                (
                    PolicyViolation("A_CODE", "maxLength", "first reason"),
                    PolicyViolation("B_CODE", "batchSize", "second reason"),
                )
            )
        )

        assert detail == [
            {"type": "A_CODE", "loc": ["body", "training", "maxLength"],
             "msg": "first reason"},
            {"type": "B_CODE", "loc": ["body", "training", "batchSize"],
             "msg": "second reason"},
        ]

    def test_an_empty_report_produces_no_errors(self):
        from app.api.scheduler import _as_error_detail

        assert _as_error_detail(PolicyReport(())) == []


class TestAutoOutputPassesThisCheck:
    """AUTO가 만든 설정이 CUSTOM 검사에서 걸리면 두 규칙이 어긋난 것이다."""

    @pytest.mark.parametrize(
        "sample_count,max_seq_length,avg_seq_length",
        [(1000, 4096, 256.0), (10, 128, 80.0), (1, 1, 1.0), (16, 512, 512.0)],
    )
    def test_every_mode_on_every_dataset(
        self, sample_count, max_seq_length, avg_seq_length
    ):
        from app.schemas.training import TrainingMode
        from app.scheduler.auto_config import build_auto_config

        metadata = dataset(
            sampleCount=sample_count,
            maxSeqLength=max_seq_length,
            avgSeqLength=avg_seq_length,
            totalTokens=max(1, sample_count),
        )

        for mode in TrainingMode:
            report = check_training_policy(build_auto_config(mode, metadata), metadata)

            assert report.is_acceptable, (mode, report.violations)
