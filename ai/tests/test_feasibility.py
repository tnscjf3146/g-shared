"""돌릴 수 있는 GPU만 남긴다. 규칙 하나뿐이라 확인할 것은 경계와 순서다.

여기서 확인하지 않는 것: 어느 GPU가 더 빠른가. 그건 예측이 답하고 순위가 정한다.
이 파일은 "돌아가는가"만 본다.
"""

import pytest

from app.schemas.recommendation import JobRequirements
from app.schemas.runtime import GpuCandidate
from app.scheduler.feasibility import NoFeasibleGpuError, filter_feasible_candidates


def candidate(worker_id, vram_mb, benchmark=36521.7):
    return GpuCandidate(
        worker_id=worker_id,
        gpu_model="NVIDIA GeForce RTX 4070 Laptop GPU",
        vram_mb=vram_mb,
        benchmark_tokens_per_sec=benchmark,
    )


def requiring(min_vram_mb):
    return JobRequirements(min_vram_mb=min_vram_mb)


class TestWhatSurvives:
    def test_a_gpu_with_more_vram_than_required(self):
        survivors = filter_feasible_candidates(
            requiring(8000), [candidate("worker-a", 12288)])

        assert [entry.worker_id for entry in survivors] == ["worker-a"]

    def test_a_gpu_with_exactly_the_required_vram(self):
        # >= 다. 요구량과 정확히 같은 VRAM은 모자란 것이 아니다.
        survivors = filter_feasible_candidates(
            requiring(12288), [candidate("worker-exact", 12288)])

        assert [entry.worker_id for entry in survivors] == ["worker-exact"]

    def test_a_gpu_one_mib_short_does_not(self):
        with pytest.raises(NoFeasibleGpuError):
            filter_feasible_candidates(
                requiring(12288), [candidate("worker-short", 12287)])

    def test_only_the_ones_that_fit(self):
        survivors = filter_feasible_candidates(requiring(12000), [
            candidate("worker-8g", 8192),
            candidate("worker-12g", 12288),
            candidate("worker-24g", 24576),
        ])

        assert [entry.worker_id for entry in survivors] == ["worker-12g", "worker-24g"]

    def test_all_of_them_when_the_requirement_is_low(self):
        survivors = filter_feasible_candidates(requiring(8000), [
            candidate("worker-8g", 8192),
            candidate("worker-12g", 12288),
            candidate("worker-24g", 24576),
        ])

        assert len(survivors) == 3

    def test_the_candidates_come_back_untouched(self):
        # 걸러내기만 한다. 값을 고치거나 감싸지 않는다.
        given = candidate("worker-a", 12288)

        assert filter_feasible_candidates(requiring(8000), [given]) == (given,)


class TestTheOrderIsNotARanking:
    """받은 순서를 그대로 돌려준다. 여기서 정렬하면 순위를 정한 것처럼 보인다."""

    def test_the_input_order_is_kept(self):
        survivors = filter_feasible_candidates(requiring(8000), [
            candidate("worker-c", 24576),
            candidate("worker-a", 8192),
            candidate("worker-b", 12288),
        ])

        assert [entry.worker_id for entry in survivors] == [
            "worker-c", "worker-a", "worker-b"]

    def test_it_does_not_sort_by_vram(self):
        survivors = filter_feasible_candidates(requiring(8000), [
            candidate("worker-small", 8192),
            candidate("worker-big", 24576),
        ])

        assert survivors[0].worker_id == "worker-small"

    def test_it_does_not_sort_by_benchmark(self):
        survivors = filter_feasible_candidates(requiring(8000), [
            candidate("worker-slow", 12288, benchmark=17000.0),
            candidate("worker-fast", 12288, benchmark=72000.0),
        ])

        assert survivors[0].worker_id == "worker-slow"


class TestWhenNothingFits:
    """빈 목록을 돌려주지 않는다. 받은 쪽이 다음 단계에서 터지면 원인이 가려진다."""

    def test_it_is_refused(self):
        with pytest.raises(NoFeasibleGpuError):
            filter_feasible_candidates(requiring(24576), [
                candidate("worker-8g", 8192),
                candidate("worker-12g", 12288),
            ])

    def test_the_message_says_what_was_required(self):
        with pytest.raises(NoFeasibleGpuError, match="24576"):
            filter_feasible_candidates(
                requiring(24576), [candidate("worker-8g", 8192)])

    def test_the_message_says_how_many_were_considered(self):
        with pytest.raises(NoFeasibleGpuError, match="2 GPU candidates"):
            filter_feasible_candidates(requiring(24576), [
                candidate("worker-8g", 8192),
                candidate("worker-12g", 12288),
            ])

    def test_the_error_carries_both_numbers(self):
        # 문구가 바뀌어도 코드가 읽을 수 있어야 한다.
        with pytest.raises(NoFeasibleGpuError) as raised:
            filter_feasible_candidates(requiring(24576), [candidate("worker-a", 8192)])

        assert raised.value.min_vram_mb == 24576
        assert raised.value.considered == 1

    def test_the_biggest_gpu_is_not_offered_as_a_consolation(self):
        # "제일 큰 걸로 해 드렸습니다"는 못 돌린다는 답을 돌릴 수 있다는 답으로
        # 바꿔치기하는 것이다.
        with pytest.raises(NoFeasibleGpuError):
            filter_feasible_candidates(requiring(40960), [
                candidate("worker-8g", 8192),
                candidate("worker-24g", 24576),
            ])


class TestTheRequirement:
    def test_zero_is_not_a_requirement(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            requiring(0)

    def test_a_negative_requirement_is_refused(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            requiring(-1)

    def test_a_numeric_string_is_refused(self):
        # strict=True. JSON에는 숫자 타입이 있으므로 문자열로 왔다면 보낸 쪽이
        # 잘못 만든 것이고, 조용히 고쳐 주면 그 잘못이 계속 남는다.
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            JobRequirements(min_vram_mb="12000")
