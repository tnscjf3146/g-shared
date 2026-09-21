"""실측 칸만 쓰고, 칸이 없으면 Backend가 보낸 값을 그대로 쓰는지.

숫자는 모두 2026-09-21 calibration(E01~E06)의 결과다. 여기서 다시 계산하지 않는다.
"""

import pytest

from app.scheduler.vram_requirement import (
    CALIBRATED_REQUIREMENTS_MIB,
    QWEN3_0_6B_MODEL_ID,
    QWEN3_1_7B_MODEL_ID,
    VramRequirementSource,
    effective_sequence_length,
    estimate_required_vram_mb,
)

LEGACY = 8192
Q06 = QWEN3_0_6B_MODEL_ID
Q17 = QWEN3_1_7B_MODEL_ID


def required(model_id, batch, max_length, max_seq_length=None, legacy=LEGACY):
    return estimate_required_vram_mb(
        model_id=model_id,
        batch_size=batch,
        max_length=max_length,
        max_seq_length=max_length if max_seq_length is None else max_seq_length,
        legacy_min_vram_mb=legacy,
    )


class TestTheMeasuredPoints:
    @pytest.mark.parametrize(("model_id", "batch", "length", "expected"), [
        (Q06, 4, 127, 4608),     # E01
        (Q06, 8, 127, 7168),     # E02
        (Q06, 4, 511, 13312),    # E03
        (Q17, 4, 127, 7168),     # E04
        (Q17, 8, 127, 11264),    # E05
        (Q17, 4, 511, 19456),    # E06
    ])
    def test_each_experiment_gets_its_calibrated_value(self, model_id, batch, length, expected):
        result = required(model_id, batch, length)

        assert result.required_vram_mb == expected
        assert result.source is VramRequirementSource.CALIBRATED_RULE

    def test_the_model_ids_are_the_backend_catalog_rows(self):
        # V8: qwen2.5-7b = 1, V12: qwen3-0.6b = 2, qwen3-1.7b = 3
        assert (Q06, Q17) == (2, 3)
        assert set(CALIBRATED_REQUIREMENTS_MIB) == {2, 3}


class TestTheCeiling:
    @pytest.mark.parametrize(("batch", "length", "expected"), [
        (3, 100, 4608),      # → B4/L128
        (6, 100, 7168),      # → B8/L128
        (4, 300, 13312),     # → B4/L512
        (1, 1, 4608),        # 가장 작은 칸
        (4, 128, 4608),      # 칸의 모서리 자체
        (8, 128, 7168),
        (4, 512, 13312),
        (5, 128, 7168),      # batch만 한 칸 넘으면 다음 칸
        (4, 129, 13312),     # 길이만 한 칸 넘으면 다음 칸
    ])
    def test_the_smallest_cell_that_covers_the_request(self, batch, length, expected):
        result = required(Q06, batch, length)

        assert result.required_vram_mb == expected
        assert result.source is VramRequirementSource.CALIBRATED_RULE

    def test_the_calibrated_value_wins_over_a_larger_legacy_value(self):
        # 8192는 지금 모델 하한이 아니라 일괄 값이다. max(8192, 4608)이면 이 작업이 무의미하다.
        assert required(Q06, 4, 127, legacy=8192).required_vram_mb == 4608

    def test_the_calibrated_value_wins_over_a_smaller_legacy_value(self):
        assert required(Q17, 4, 511, legacy=8192).required_vram_mb == 19456


class TestWhatWasNotMeasured:
    @pytest.mark.parametrize(("model_id", "batch", "length"), [
        (Q06, 16, 128),
        (Q06, 8, 512),
        (Q17, 16, 128),
        (Q17, 8, 512),
        (Q06, 4, 513),       # 가장 긴 칸보다 길다
        (Q06, 9, 100),       # 가장 큰 batch 칸보다 크다
    ])
    def test_an_unmeasured_combination_keeps_the_legacy_value(self, model_id, batch, length):
        result = required(model_id, batch, length)

        assert result.required_vram_mb == LEGACY
        assert result.source is VramRequirementSource.LEGACY_FALLBACK

    @pytest.mark.parametrize("model_id", [1, 4, 999])
    def test_an_unknown_model_keeps_the_legacy_value(self, model_id):
        result = required(model_id, 4, 127)

        assert result.required_vram_mb == LEGACY
        assert result.source is VramRequirementSource.LEGACY_FALLBACK

    def test_the_legacy_value_is_passed_through_unchanged(self):
        assert required(Q06, 16, 512, legacy=12345).required_vram_mb == 12345


class TestTheEffectiveLength:
    def test_it_is_the_shorter_of_max_length_and_the_longest_sample(self):
        assert effective_sequence_length(512, 127) == 127
        assert effective_sequence_length(128, 300) == 128
        assert effective_sequence_length(256, 256) == 256

    def test_short_data_under_a_long_max_length_uses_the_short_cell(self):
        # maxLength 2048이어도 가장 긴 샘플이 120이면 GPU에 올라가는 길이는 120이다.
        assert required(Q06, 4, 2048, max_seq_length=120).required_vram_mb == 4608

    def test_long_data_is_cut_to_max_length(self):
        # 데이터가 1000토큰이어도 maxLength 128에서 잘린다.
        assert required(Q06, 8, 128, max_seq_length=1000).required_vram_mb == 7168

    def test_long_data_under_an_unmeasured_max_length_falls_back(self):
        assert required(Q06, 4, 1024, max_seq_length=1000).source is (
            VramRequirementSource.LEGACY_FALLBACK)
