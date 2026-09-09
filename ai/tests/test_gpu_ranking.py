"""줄 세우기. 규칙은 하나지만, 재현성과 방어가 이 파일의 대부분이다.

같은 요청을 두 번 보낸 Backend가 다른 Worker를 추천받으면 안 된다. 그것이
Tie 처리와 입력 순서 무관성을 여기서 못 박는 이유다.
"""

import math

import pytest

from app.scheduler.ranking import RankingError, rank_candidates, recommend


class TestOrdering:
    def test_one_candidate_is_rank_one(self):
        ranked = rank_candidates([("worker-a", 1000.0)])

        assert len(ranked) == 1
        assert ranked[0].rank == 1
        assert ranked[0].worker_id == "worker-a"

    def test_the_faster_of_two_comes_first(self):
        ranked = rank_candidates([("worker-slow", 8300.0), ("worker-fast", 3721.0)])

        assert [entry.worker_id for entry in ranked] == ["worker-fast", "worker-slow"]

    def test_several_candidates_are_ascending_by_predicted_seconds(self):
        ranked = rank_candidates([
            ("worker-c", 13691.0), ("worker-a", 8300.0), ("worker-b", 3721.0),
            ("worker-d", 15369.0), ("worker-e", 4933.0),
        ])

        seconds = [entry.predicted_runtime_sec for entry in ranked]
        assert seconds == sorted(seconds)

    def test_rank_starts_at_one_and_has_no_gaps(self):
        ranked = rank_candidates([(f"worker-{n}", 1000.0 * (5 - n)) for n in range(5)])

        assert [entry.rank for entry in ranked] == [1, 2, 3, 4, 5]

    def test_every_candidate_appears_exactly_once(self):
        given = [("worker-a", 300.0), ("worker-b", 100.0), ("worker-c", 200.0)]

        ranked = rank_candidates(given)

        assert sorted(entry.worker_id for entry in ranked) == [
            "worker-a", "worker-b", "worker-c"]

    def test_the_predicted_seconds_are_carried_through_untouched(self):
        # 반올림하지 않는다. 표시 형식은 화면이 정할 일이다.
        ranked = rank_candidates([("worker-a", 3720.998779296875)])

        assert ranked[0].predicted_runtime_sec == 3720.998779296875


class TestTheInputOrderDoesNotMatter:
    """같은 후보들을 어떤 순서로 넣어도 같은 줄이 나와야 한다."""

    CANDIDATES = [("worker-c", 13691.0), ("worker-a", 8300.0), ("worker-b", 3721.0)]

    def test_reversing_the_input_gives_the_same_ranking(self):
        forwards = rank_candidates(self.CANDIDATES)
        backwards = rank_candidates(list(reversed(self.CANDIDATES)))

        assert forwards == backwards

    def test_every_permutation_gives_the_same_ranking(self):
        from itertools import permutations

        expected = rank_candidates(self.CANDIDATES)

        for order in permutations(self.CANDIDATES):
            assert rank_candidates(list(order)) == expected


class TestTies:
    """예측이 글자 그대로 같을 때. workerId로 가른다."""

    def test_an_exact_tie_is_broken_by_worker_id(self):
        ranked = rank_candidates([("worker-b", 1000.0), ("worker-a", 1000.0)])

        assert [entry.worker_id for entry in ranked] == ["worker-a", "worker-b"]

    def test_the_tie_break_does_not_depend_on_input_order(self):
        first = rank_candidates([("worker-b", 1000.0), ("worker-a", 1000.0)])
        second = rank_candidates([("worker-a", 1000.0), ("worker-b", 1000.0)])

        assert first == second
        assert recommend(first) == recommend(second) == "worker-a"

    def test_a_three_way_tie_is_ordered_by_worker_id(self):
        ranked = rank_candidates([
            ("worker-c", 500.0), ("worker-a", 500.0), ("worker-b", 500.0)])

        assert [entry.worker_id for entry in ranked] == [
            "worker-a", "worker-b", "worker-c"]

    def test_a_tie_within_a_larger_field_only_affects_the_tied_pair(self):
        ranked = rank_candidates([
            ("worker-slow", 9000.0), ("worker-b", 1000.0),
            ("worker-a", 1000.0), ("worker-fastest", 100.0)])

        assert [entry.worker_id for entry in ranked] == [
            "worker-fastest", "worker-a", "worker-b", "worker-slow"]

    def test_nearly_equal_floats_are_not_treated_as_a_tie(self):
        """1e-7초 차이는 값으로 가른다. epsilon을 두지 않는 이유가 있다.

        "거의 같으면 같다"는 비교는 추이적이지 않아 -- a~b, b~c 인데 a와 c는
        다를 수 있어 -- 정렬 결과가 입력 순서를 타게 된다. 재현성을 위해 넣은
        규칙이 재현성을 깨뜨리는 셈이다. 정확한 값으로 비교하면 이 두 후보의
        순서도 언제나 같고, 어차피 어느 쪽을 골라도 같은 선택이다.
        """
        ranked = rank_candidates([
            ("worker-a", 1000.0000002), ("worker-b", 1000.0000001)])

        assert [entry.worker_id for entry in ranked] == ["worker-b", "worker-a"]

    def test_and_that_answer_is_stable(self):
        pair = [("worker-a", 1000.0000002), ("worker-b", 1000.0000001)]

        assert rank_candidates(pair) == rank_candidates(list(reversed(pair)))


class TestRecommendation:
    def test_it_is_the_fastest(self):
        ranked = rank_candidates([
            ("worker-a", 8300.0), ("worker-b", 3721.0), ("worker-c", 13691.0)])

        assert recommend(ranked) == "worker-b"

    def test_a_single_candidate_is_its_own_recommendation(self):
        assert recommend(rank_candidates([("worker-only", 42.0)])) == "worker-only"

    def test_it_is_always_the_rank_one_entry(self):
        ranked = rank_candidates([("worker-a", 300.0), ("worker-b", 100.0)])

        assert recommend(ranked) == ranked[0].worker_id


class TestWhatCannotBeRanked:
    def test_no_candidates_at_all(self):
        # 빈 목록을 돌려주면 1위를 꺼내는 쪽이 터진다.
        with pytest.raises(RankingError, match="no GPU candidates"):
            rank_candidates([])

    def test_nothing_to_recommend(self):
        with pytest.raises(RankingError, match="nothing to recommend"):
            recommend([])

    @pytest.mark.parametrize("bad", [0, 0.0, -1.0, -3721.0])
    def test_a_runtime_of_zero_or_less(self, bad):
        # 0초에 끝나는 GPU는 1위가 되고, 그 이유로 추천된다.
        with pytest.raises(RankingError, match="greater than zero"):
            rank_candidates([("worker-a", 1000.0), ("worker-b", bad)])

    @pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
    def test_a_runtime_that_is_not_finite(self, bad):
        with pytest.raises(RankingError, match="finite"):
            rank_candidates([("worker-a", 1000.0), ("worker-b", bad)])

    @pytest.mark.parametrize("bad", ["1000.0", None, True])
    def test_a_runtime_that_is_not_a_number(self, bad):
        with pytest.raises(RankingError, match="must be a number"):
            rank_candidates([("worker-a", bad)])

    def test_the_bad_candidate_is_named(self):
        with pytest.raises(RankingError, match="worker-broken"):
            rank_candidates([("worker-ok", 100.0), ("worker-broken", math.nan)])

    def test_nothing_is_substituted_for_a_bad_value(self):
        # 999999나 평균으로 바꿔 계속 진행하지 않는다. 줄을 세우지 않는다.
        with pytest.raises(RankingError):
            rank_candidates([("worker-a", 100.0), ("worker-b", 0.0)])
