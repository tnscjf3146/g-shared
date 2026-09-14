"""돌릴 수 있는 GPU만 남긴다. 규칙 하나뿐이라 확인할 것은 경계와 순서다.

여기서 확인하지 않는 것: 어느 GPU가 더 빠른가. 그건 예측이 답하고 순위가 정한다.
이 파일은 "돌아가는가"만 본다.
"""

import pytest

from app.schemas.recommendation import JobRequirements
from app.schemas.runtime import GpuCandidate
from app.scheduler.feasibility import (
    NOMINAL_CAPACITY_TOLERANCE_MIB,
    VRAM_CAPACITY_STEP_MIB,
    NoFeasibleGpuError,
    filter_feasible_candidates,
    nominal_vram_mib,
)


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
        # 공칭 용량으로 읽은 뒤에도 요구량보다 1 MiB 모자라면 탈락이다. (예전에는
        # 12287 대 12288로 적었지만, 12287은 이제 12 GiB 카드의 보고값으로 읽힌다 --
        # TestNominalCapacity 참고. 경계의 뜻은 그대로 12288 대 12289로 옮겼다.)
        with pytest.raises(NoFeasibleGpuError):
            filter_feasible_candidates(
                requiring(12289), [candidate("worker-short", 12288)])

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


class TestNominalCapacity:
    """드라이버 보고값을 판매 용량(GiB 단위)으로 읽고 비교한다. 비교할 때만.

    쓰인 숫자는 지어낸 값이 아니라 이 프로젝트가 받은 보고값이다(data/ 의 실측·
    부트스트랩 행): RTX 4070 Laptop·RTX 4060 8188, RTX 3060 12288, RTX 4080 16376,
    RTX 4090 24564.
    """

    def test_an_8_gib_gpu_reporting_8188_runs_an_8192_job(self):
        survivors = filter_feasible_candidates(
            requiring(8192), [candidate("worker-4070-laptop", 8188)])

        assert [entry.worker_id for entry in survivors] == ["worker-4070-laptop"]

    def test_an_exact_8192_still_runs_an_8192_job(self):
        assert filter_feasible_candidates(
            requiring(8192), [candidate("worker-8g", 8192)])

    @pytest.mark.parametrize("reported, nominal", [
        (8188, 8192),      # RTX 4070 Laptop, RTX 4060
        (12288, 12288),    # RTX 3060 -- 이미 경계
        (16376, 16384),    # RTX 4080
        (24564, 24576),    # RTX 4090
        (24576, 24576),
    ])
    def test_the_reports_this_project_has_seen(self, reported, nominal):
        assert nominal_vram_mib(reported) == nominal

    def test_the_tolerance_edge_is_included(self):
        edge = 8192 - NOMINAL_CAPACITY_TOLERANCE_MIB

        assert nominal_vram_mib(edge) == 8192

    def test_one_mib_past_the_tolerance_is_left_alone(self):
        outside = 8192 - NOMINAL_CAPACITY_TOLERANCE_MIB - 1

        assert nominal_vram_mib(outside) == outside

    def test_a_gpu_just_outside_the_tolerance_does_not_run_an_8192_job(self):
        outside = 8192 - NOMINAL_CAPACITY_TOLERANCE_MIB - 1

        with pytest.raises(NoFeasibleGpuError):
            filter_feasible_candidates(requiring(8192), [candidate("worker-a", outside)])

    @pytest.mark.parametrize("reported", [7600, 8100, 12000, 3000, 5238])
    def test_a_gpu_that_is_really_smaller_is_not_rounded_up(self, reported):
        assert nominal_vram_mib(reported) == reported

    def test_a_really_short_gpu_still_fails_an_8192_job(self):
        with pytest.raises(NoFeasibleGpuError):
            filter_feasible_candidates(requiring(8192), [candidate("worker-a", 7600)])

    def test_it_never_lowers_a_value(self):
        for reported in range(1, 40000, 97):
            assert nominal_vram_mib(reported) >= reported

    def test_it_never_adds_more_than_the_tolerance(self):
        for reported in range(1, 40000, 97):
            assert nominal_vram_mib(reported) - reported <= NOMINAL_CAPACITY_TOLERANCE_MIB

    def test_an_8188_gpu_is_not_treated_as_a_12_gib_gpu(self):
        with pytest.raises(NoFeasibleGpuError):
            filter_feasible_candidates(
                requiring(12288), [candidate("worker-4070-laptop", 8188)])

    def test_the_reported_value_is_not_rewritten(self):
        # 원래 vramMb는 예측 feature이자 실측 기록이다. 걸러내기가 고쳐 쓰지 않는다.
        given = candidate("worker-4070-laptop", 8188)

        survivors = filter_feasible_candidates(requiring(8192), [given])

        assert survivors == (given,)
        assert survivors[0].vram_mb == 8188

    def test_the_named_constants(self):
        assert VRAM_CAPACITY_STEP_MIB == 1024
        assert NOMINAL_CAPACITY_TOLERANCE_MIB == 32


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
