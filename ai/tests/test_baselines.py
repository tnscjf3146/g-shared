"""XGBoost가 비교 없이 채택되지 않도록.

MAPE 15%가 좋은 숫자인지 나쁜 숫자인지는 그 자체로 알 수 없다. 아무것도 학습하지
않았을 때 몇 %인지를 알아야 말할 수 있고, 그래서 학습할 때마다 셋을 함께 잰다.
"""

from __future__ import annotations

import numpy as np

from app.training.baselines import (
    compare,
    median_prediction,
    score,
    throughput_prediction,
)


def a_row(tokens=1000, epochs=1, rate=100.0):
    return {
        "effective_tokens": tokens,
        "epochs": epochs,
        "benchmark_tokens_per_sec": rate,
    }


class TestScoring:
    def test_a_perfect_prediction_scores_zero(self):
        result = score("x", np.array([10.0, 20.0]), np.array([10.0, 20.0]))

        assert result.mae_sec == 0.0
        assert result.mape_percent == 0.0

    def test_the_error_is_in_seconds(self):
        result = score("x", np.array([100.0]), np.array([130.0]))

        assert result.mae_sec == 30.0

    def test_the_relative_error_is_a_percentage(self):
        result = score("x", np.array([100.0]), np.array([130.0]))

        assert result.mape_percent == 30.0


class TestTheMedianBaseline:
    def test_it_answers_the_same_thing_every_time(self):
        predicted = median_prediction(np.array([1.0, 5.0, 100.0]), count=4)

        assert list(predicted) == [5.0, 5.0, 5.0, 5.0]

    def test_it_is_the_median_and_not_the_mean(self):
        # 실행 시간 분포는 한쪽으로 길게 끌린다. 평균은 가장 긴 몇 건에 끌려간다.
        predicted = median_prediction(np.array([10.0, 10.0, 10.0, 10_000.0]), 1)

        assert predicted[0] == 10.0


class TestTheThroughputBaseline:
    def test_tokens_divided_by_rate(self):
        predicted = throughput_prediction([a_row(tokens=1000, rate=100.0)])

        assert list(predicted) == [10.0]

    def test_epochs_multiply(self):
        predicted = throughput_prediction([a_row(tokens=1000, epochs=3, rate=100.0)])

        assert list(predicted) == [30.0]

    def test_a_zero_rate_gives_up_rather_than_dividing(self):
        assert throughput_prediction([a_row(rate=0.0)]) is None

    def test_a_negative_rate_gives_up(self):
        assert throughput_prediction([a_row(rate=-5.0)]) is None

    def test_a_missing_column_gives_up(self):
        assert throughput_prediction([{"epochs": 1}]) is None

    def test_one_bad_row_gives_up_on_all_of_them(self):
        # 일부만 계산한 평균은 다른 baseline과 같은 검증셋에서 잰 값이 아니라
        # 비교할 수 없다.
        assert throughput_prediction([a_row(), a_row(rate=0.0)]) is None


class TestTheComparison:
    def setup_method(self):
        self.train = np.array([100.0, 200.0, 300.0])
        self.valid = np.array([100.0, 200.0])
        # throughput은 150s / 300s를 답한다 -- 실제의 1.5배다. 일부러 정확하지
        # 않게 두었다. 토큰 수를 딱 맞추면 baseline이 완벽해져서 "XGBoost가
        # baseline을 이긴다"는 테스트가 이길 수 없는 대상과 겨루게 된다.
        self.rows = [a_row(tokens=15_000, rate=100.0),
                     a_row(tokens=30_000, rate=100.0)]

    def test_all_three_are_scored(self):
        result = compare(train_target=self.train, valid_target=self.valid,
                         valid_rows=self.rows,
                         model_predicted=np.array([101.0, 199.0]))

        assert {entry["name"] for entry in result["scores"]} == {
            "median", "throughput", "xgboost"}

    def test_a_good_model_beats_the_baselines(self):
        result = compare(train_target=self.train, valid_target=self.valid,
                         valid_rows=self.rows,
                         model_predicted=np.array([100.0, 200.0]))

        assert result["beatsBaselines"]
        assert result["best"] == "xgboost"

    def test_a_bad_model_is_recorded_as_losing_rather_than_hidden(self):
        # 학습을 실패시키지 않는다. 지금 데이터에서 무엇이 이겼는지는 읽는 사람이
        # 알아야 할 결과이지 감출 일이 아니다.
        result = compare(train_target=self.train, valid_target=self.valid,
                         valid_rows=self.rows,
                         model_predicted=np.array([9000.0, 9000.0]))

        assert not result["beatsBaselines"]
        assert result["best"] != "xgboost"

    def test_the_throughput_baseline_is_skipped_when_unusable(self):
        result = compare(train_target=self.train, valid_target=self.valid,
                         valid_rows=[{"nothing": 1}, {"nothing": 2}],
                         model_predicted=np.array([100.0, 200.0]))

        assert {entry["name"] for entry in result["scores"]} == {
            "median", "xgboost"}

    def test_it_still_compares_against_the_median_in_that_case(self):
        result = compare(train_target=self.train, valid_target=self.valid,
                         valid_rows=[{}, {}],
                         model_predicted=np.array([100.0, 200.0]))

        assert result["beatsBaselines"]
