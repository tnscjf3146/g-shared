"""출처가 학습 데이터를 어떻게 거르는가.

이 파일 전체가 §9 "데이터 정직성"을 코드로 붙잡아 두기 위한 것이다. 가장 중요한
것 하나를 먼저 적자면: **calibration 행은 feature가 완전히 정상이라 다른 어떤
검증에도 걸리지 않는다.** 40-step에서 끊은 실행과 같은 설정을 끝까지 돌린 실행은
11개 feature가 똑같고 target만 다르다. 출처 표시를 지우면 그 순간부터 모순된 두
답이 조용히 학습된다.
"""

from __future__ import annotations

import pytest

from app.training.provenance import (
    Provenance,
    ProvenanceError,
    is_production_ready,
    limitations_for,
    provenance_of,
    select_trainable,
)


def row(kind, **extra):
    body = {"provenance": kind.value if isinstance(kind, Provenance) else kind}
    body.update(extra)
    return body


class TestEveryRowMustSayWhereItCameFrom:
    def test_a_labelled_row_is_read(self):
        assert provenance_of(row(Provenance.MEASURED)) is Provenance.MEASURED

    def test_an_unlabelled_row_is_refused(self):
        # 기본값을 주지 않는다. BOOTSTRAP으로 가정하면 실측이 합성으로 기록되고,
        # MEASURED로 가정하면 합성이 실측으로 둔갑한다. 둘 다 되돌릴 수 없다.
        with pytest.raises(ProvenanceError):
            provenance_of({"actual_runtime_sec": 100.0})

    def test_the_refusal_names_the_row(self):
        with pytest.raises(ProvenanceError, match="row 7"):
            provenance_of({}, 7)

    def test_an_unknown_label_is_refused(self):
        with pytest.raises(ProvenanceError, match="ESTIMATED"):
            provenance_of(row("ESTIMATED"))

    def test_the_vocabulary_is_exactly_four(self):
        assert {p.value for p in Provenance} == {
            "MEASURED", "CALIBRATION", "BOOTSTRAP", "PRODUCTION"}


class TestCalibrationNeverBecomesATarget:
    """이 파일에서 가장 중요한 클래스."""

    def test_a_calibration_row_is_dropped(self):
        kept, counts = select_trainable([row(Provenance.CALIBRATION)])

        assert kept == []
        assert counts == {"CALIBRATION": 1}

    def test_it_is_dropped_even_beside_usable_rows(self):
        kept, _ = select_trainable([
            row(Provenance.MEASURED, run_id="full"),
            row(Provenance.CALIBRATION, run_id="cut-at-40"),
            row(Provenance.BOOTSTRAP, run_id="synthetic"),
        ])

        assert [r["run_id"] for r in kept] == ["full", "synthetic"]

    def test_a_file_mixing_both_is_not_an_error(self):
        # calibration 기록을 같은 파일에 모아 두는 것은 정상이다. 학습이 빼면 된다.
        kept, counts = select_trainable(
            [row(Provenance.CALIBRATION)] * 4 + [row(Provenance.BOOTSTRAP)] * 2)

        assert len(kept) == 2
        assert counts == {"BOOTSTRAP": 2, "CALIBRATION": 4}

    def test_the_exclusion_is_reported_as_a_limitation(self):
        notes = limitations_for({"CALIBRATION": 4, "MEASURED": 2})

        assert any("CALIBRATION" in note and "partial" in note for note in notes)


class TestWhatCountsAsTrainable:
    @pytest.mark.parametrize("kind", [Provenance.MEASURED, Provenance.PRODUCTION,
                                      Provenance.BOOTSTRAP])
    def test_these_can_be_trained_on(self, kind):
        kept, _ = select_trainable([row(kind)])

        assert len(kept) == 1

    def test_nothing_usable_is_reported_rather_than_silently_empty(self):
        kept, counts = select_trainable([row(Provenance.CALIBRATION)] * 3)

        assert kept == []
        assert counts["CALIBRATION"] == 3


class TestProductionReadiness:
    def test_bootstrap_is_never_production_ready(self):
        assert not is_production_ready({"BOOTSTRAP": 1200})

    def test_bootstrap_mixed_with_real_data_is_still_not_ready(self):
        # 한 줄이라도 섞이면 아니다. 어느 예측에 그 줄이 영향을 줬는지 알 수 없다.
        assert not is_production_ready({"BOOTSTRAP": 1200, "MEASURED": 2})

    def test_real_data_alone_is_ready(self):
        assert is_production_ready({"MEASURED": 40, "PRODUCTION": 300})

    def test_nothing_at_all_is_not_ready(self):
        assert not is_production_ready({})

    def test_calibration_does_not_count_towards_readiness(self):
        assert not is_production_ready({"CALIBRATION": 500})


class TestLimitations:
    def test_bootstrap_data_is_called_out(self):
        notes = limitations_for({"BOOTSTRAP": 1200})

        assert any("BOOTSTRAP" in note for note in notes)

    def test_the_bootstrap_note_says_what_the_error_actually_measures(self):
        notes = limitations_for({"BOOTSTRAP": 1200})

        assert any("generator" in note for note in notes)

    def test_having_no_measured_rows_is_called_out(self):
        assert any("No MEASURED" in note
                   for note in limitations_for({"BOOTSTRAP": 10}))

    def test_a_single_gpu_is_called_out(self):
        notes = limitations_for(
            {"MEASURED": 2}, measured_gpu_models=["RTX 4070 Laptop"] * 2)

        assert any("single GPU" in note for note in notes)
        assert any("RTX 4070 Laptop" in note for note in notes)

    def test_several_gpus_drops_that_note(self):
        notes = limitations_for(
            {"MEASURED": 9},
            measured_gpu_models=["RTX 4070", "RTX 3060", "A100"])

        assert not any("single GPU" in note for note in notes)

    def test_synthetic_gpu_variety_does_not_count_as_validation(self):
        """처음 구현했을 때 실제로 틀렸던 부분.

        부트스트랩 생성기는 GPU 다섯 종류를 만들어 낸다. 그 이름들을 넘기면
        "여러 GPU를 덮었다"가 참이 되어 cross-GPU 경고가 사라졌다 -- 실측은 한
        대뿐인데도. 합성 행은 아예 넘기지 않는 것이 답이고, 넘길 것이 없을 때는
        경고가 더 강해져야 한다.
        """
        notes = limitations_for({"BOOTSTRAP": 1200}, measured_gpu_models=[])

        assert any("has not been validated against any" in note
                   for note in notes)
        assert any("synthetic and is not evidence" in note for note in notes)
