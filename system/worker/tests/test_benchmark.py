"""Measuring the GPU, and everything that must never be mistaken for a number.

The rule this file exists to hold: **measured or nothing**. Zero is not a
measurement, neither is NaN, neither is a result from a different workload, and
neither is a container that exited 0 having printed nothing. Every one of them
is a failure here, because a scheduler cannot tell an invented number from a
real one.
"""

from __future__ import annotations

import json
import unittest

from gshared_worker.benchmark import (
    BENCHMARK_IMAGE,
    BenchmarkOutcome,
    BenchmarkReport,
    GpuBenchmark,
    read_result,
)
from gshared_worker.benchmark_program import (
    BENCHMARK_VERSION,
    MEASURED_STEPS,
    PROCESSED_TOKENS_PER_RUN,
    RESULT_MARKER,
    RUNS,
)
from gshared_worker.command_runner import CommandResult
from gshared_worker.errors import CommandError

RATE = 1240.7


def a_run(rate=RATE):
    return {"processedTokens": PROCESSED_TOKENS_PER_RUN,
            "elapsedSeconds": PROCESSED_TOKENS_PER_RUN / rate,
            "tokensPerSec": rate}


def a_result(**overrides):
    payload = {
        "benchmarkVersion": BENCHMARK_VERSION,
        "status": "OK",
        "tokensPerSec": RATE,
        "processedTokens": PROCESSED_TOKENS_PER_RUN,
        "elapsedSeconds": PROCESSED_TOKENS_PER_RUN / RATE,
        "batchSize": 4,
        "sequenceLength": 512,
        "warmupSteps": 8,
        "measuredSteps": MEASURED_STEPS,
        "runs": [a_run() for _ in range(RUNS)],
        "peakVramMb": 2172,
    }
    payload.update(overrides)
    return payload


def printed(payload, *, before="") -> str:
    return before + RESULT_MARKER + json.dumps(payload) + "\n"


class ReadingAGoodMeasurementTest(unittest.TestCase):
    def test_the_rate_is_what_the_container_measured(self):
        report = read_result(printed(a_result()))

        self.assertIs(report.outcome, BenchmarkOutcome.MEASURED)
        self.assertEqual(report.result.tokens_per_sec, RATE)

    def test_what_the_rate_was_measured_from_comes_with_it(self):
        result = read_result(printed(a_result())).result

        self.assertEqual(result.processed_tokens, PROCESSED_TOKENS_PER_RUN)
        self.assertEqual(result.measured_steps, MEASURED_STEPS)
        self.assertEqual(result.batch_size, 4)
        self.assertEqual(result.sequence_length, 512)

    def test_processed_tokens_is_batch_times_length_times_steps(self):
        result = read_result(printed(a_result())).result

        self.assertEqual(
            result.processed_tokens,
            result.batch_size * result.sequence_length * result.measured_steps)

    def test_every_runs_rate_is_kept_so_a_wide_spread_can_be_seen(self):
        payload = a_result(runs=[a_run(1000.0), a_run(1240.7), a_run(1400.0)])

        self.assertEqual(
            read_result(printed(payload)).result.run_rates, (1000.0, 1240.7, 1400.0))

    def test_other_output_before_the_result_does_not_hide_it(self):
        noisy = printed(a_result(), before="a warning from some library\n")

        self.assertIs(read_result(noisy).outcome, BenchmarkOutcome.MEASURED)

    def test_the_summary_names_the_number_and_where_it_came_from(self):
        summary = read_result(printed(a_result())).result.summary()

        self.assertIn("tokens/sec", summary)
        self.assertIn("GPU 시간", summary)


class NumbersThatAreNotMeasurementsTest(unittest.TestCase):
    """Zero, negative, NaN and infinity are refusals, never values."""

    def refuses(self, rate):
        report = read_result(printed(a_result(tokensPerSec=rate)))
        self.assertIsNot(report.outcome, BenchmarkOutcome.MEASURED, rate)
        self.assertIsNone(report.result, rate)

    def test_zero(self):
        self.refuses(0)

    def test_zero_as_a_float(self):
        self.refuses(0.0)

    def test_negative(self):
        self.refuses(-1240.7)

    def test_nan(self):
        # json.loads accepts NaN, so this really can arrive.
        self.assertIs(
            read_result(RESULT_MARKER + json.dumps(a_result()).replace(
                str(RATE), "NaN")).outcome,
            BenchmarkOutcome.MALFORMED)

    def test_infinity(self):
        self.assertIs(
            read_result(RESULT_MARKER + json.dumps(a_result()).replace(
                str(RATE), "Infinity")).outcome,
            BenchmarkOutcome.MALFORMED)

    def test_a_string_that_looks_like_a_number(self):
        self.refuses("1240.7")

    def test_a_boolean(self):
        self.refuses(True)

    def test_null(self):
        self.refuses(None)


class OutputThatCannotBeBelievedTest(unittest.TestCase):
    def test_nothing_printed_at_all(self):
        self.assertIs(read_result("").outcome, BenchmarkOutcome.MALFORMED)

    def test_output_without_the_marker(self):
        self.assertIs(
            read_result("training finished\n").outcome, BenchmarkOutcome.MALFORMED)

    def test_the_marker_with_something_that_is_not_json(self):
        self.assertIs(
            read_result(RESULT_MARKER + "{ half a result").outcome,
            BenchmarkOutcome.MALFORMED)

    def test_a_json_array_is_not_a_result(self):
        self.assertIs(
            read_result(RESULT_MARKER + "[]").outcome, BenchmarkOutcome.MALFORMED)

    def test_a_missing_rate(self):
        payload = a_result()
        del payload["tokensPerSec"]

        self.assertIs(read_result(printed(payload)).outcome, BenchmarkOutcome.MALFORMED)

    def test_missing_what_the_rate_was_measured_from(self):
        for field in ("processedTokens", "elapsedSeconds", "measuredSteps",
                      "batchSize", "sequenceLength"):
            payload = a_result()
            del payload[field]

            self.assertIs(
                read_result(printed(payload)).outcome, BenchmarkOutcome.MALFORMED, field)

    def test_a_result_from_a_different_workload_version(self):
        # Its number cannot be compared with everybody else's.
        payload = a_result(benchmarkVersion="gshared-benchmark-v0")

        self.assertIs(read_result(printed(payload)).outcome, BenchmarkOutcome.MALFORMED)

    def test_a_result_that_measured_a_different_number_of_tokens(self):
        payload = a_result(processedTokens=PROCESSED_TOKENS_PER_RUN // 2)

        report = read_result(printed(payload))

        self.assertIs(report.outcome, BenchmarkOutcome.MALFORMED)
        self.assertIn("not the fixed", report.detail)

    def test_a_result_that_ran_a_different_number_of_steps(self):
        payload = a_result(measuredSteps=MEASURED_STEPS // 2)

        self.assertIs(read_result(printed(payload)).outcome, BenchmarkOutcome.MALFORMED)

    def test_fewer_runs_than_the_workload_fixes(self):
        payload = a_result(runs=[a_run()])

        self.assertIs(read_result(printed(payload)).outcome, BenchmarkOutcome.MALFORMED)

    def test_a_run_with_an_unusable_rate(self):
        unusable = dict(a_run(), tokensPerSec=0)
        payload = a_result(runs=[a_run(), unusable, a_run()])

        self.assertIs(read_result(printed(payload)).outcome, BenchmarkOutcome.MALFORMED)


class WhatTheContainerSaidWentWrongTest(unittest.TestCase):
    def failed(self, code):
        return read_result(printed({
            "benchmarkVersion": BENCHMARK_VERSION, "status": "FAILED",
            "errorCode": code, "errorMessage": "it did not work"}))

    def test_no_cuda_in_the_container_is_a_gpu_problem(self):
        self.assertIs(
            self.failed("BENCHMARK_CUDA_UNAVAILABLE").outcome,
            BenchmarkOutcome.GPU_UNAVAILABLE)

    def test_a_gpu_that_cannot_run_the_workload_is_a_gpu_problem(self):
        self.assertIs(
            self.failed("BENCHMARK_GPU_UNSUPPORTED").outcome,
            BenchmarkOutcome.GPU_UNAVAILABLE)

    def test_anything_else_is_a_plain_failure(self):
        self.assertIs(self.failed("BENCHMARK_FAILED").outcome, BenchmarkOutcome.FAILED)

    def test_the_reason_is_carried_through_for_the_person_to_read(self):
        report = self.failed("BENCHMARK_CUDA_UNAVAILABLE")

        self.assertIn("BENCHMARK_CUDA_UNAVAILABLE", report.detail)
        self.assertIsNone(report.result)


class Inside:
    """Stands in for the distribution, answering docker commands as told."""

    def __init__(self, **answers):
        self.answers = answers
        self.commands: list[list[str]] = []

    async def run(self, args, *, timeout, max_output_bytes):
        self.commands.append(list(args))
        key = _key(args)
        answer = self.answers.get(key, CommandResult(0, "", ""))
        if isinstance(answer, Exception):
            raise answer
        return answer

    @property
    def ran_benchmark(self) -> bool:
        return any("--entrypoint" in c for c in self.commands)

    @property
    def pulled(self) -> bool:
        return any(c[:2] == ["docker", "pull"] for c in self.commands)


def _key(args) -> str:
    if "--entrypoint" in args:
        return "run"
    if args[:2] == ["docker", "pull"]:
        return "pull"
    if args[:3] == ["docker", "image", "inspect"]:
        return "inspect"
    if args[:2] == ["docker", "version"]:
        return "version"
    return " ".join(args[:2])


def measure(inside) -> "BenchmarkReport":
    import asyncio

    return asyncio.run(GpuBenchmark(inside).measure())


class RunningItTest(unittest.TestCase):
    def test_a_measured_gpu(self):
        inside = Inside(run=CommandResult(0, printed(a_result()), ""))

        report = measure(inside)

        self.assertIs(report.outcome, BenchmarkOutcome.MEASURED)
        self.assertEqual(report.result.tokens_per_sec, RATE)

    def test_an_engine_that_does_not_answer_is_not_a_gpu_verdict(self):
        inside = Inside(version=CommandResult(1, "", "cannot connect"))

        report = measure(inside)

        self.assertIs(report.outcome, BenchmarkOutcome.ENGINE_UNAVAILABLE)
        self.assertFalse(inside.ran_benchmark)

    def test_an_engine_that_cannot_be_run_at_all(self):
        inside = Inside(version=CommandError("COMMAND_NOT_FOUND", "no wsl.exe"))

        self.assertIs(measure(inside).outcome, BenchmarkOutcome.ENGINE_UNAVAILABLE)

    def test_a_container_that_never_finished(self):
        inside = Inside(run=CommandError("COMMAND_TIMEOUT", "gave up waiting"))

        report = measure(inside)

        self.assertIs(report.outcome, BenchmarkOutcome.TIMED_OUT)
        self.assertIsNone(report.result)

    def test_a_container_that_failed_without_printing_a_result(self):
        inside = Inside(run=CommandResult(1, "", "CUDA error"))

        self.assertIs(measure(inside).outcome, BenchmarkOutcome.MALFORMED)

    def test_a_container_that_exited_zero_and_printed_nothing(self):
        # Exit code 0 is not the answer. The number is.
        inside = Inside(run=CommandResult(0, "", ""))

        self.assertIs(measure(inside).outcome, BenchmarkOutcome.MALFORMED)


class FetchingTheImageTest(unittest.TestCase):
    def test_an_image_already_here_is_not_pulled(self):
        # Every start would otherwise re-check 11 GB over the network.
        inside = Inside(inspect=CommandResult(0, '"sha256:abc"', ""),
                        run=CommandResult(0, printed(a_result()), ""))

        measure(inside)

        self.assertFalse(inside.pulled)
        self.assertTrue(inside.ran_benchmark)

    def test_a_missing_image_is_pulled_once_then_measured(self):
        answers = {"inspect": CommandResult(1, "", "No such image"),
                   "run": CommandResult(0, printed(a_result()), "")}
        inside = Inside(**answers)
        # The second inspect, after the pull, finds it.
        original = inside.run

        async def run(args, *, timeout, max_output_bytes):
            result = await original(args, timeout=timeout,
                                    max_output_bytes=max_output_bytes)
            if _key(args) == "inspect" and inside.pulled:
                return CommandResult(0, '"sha256:abc"', "")
            return result

        inside.run = run

        report = measure(inside)

        self.assertTrue(inside.pulled)
        self.assertIs(report.outcome, BenchmarkOutcome.MEASURED)

    def test_a_pull_that_failed_says_nothing_about_the_gpu(self):
        inside = Inside(inspect=CommandResult(1, "", "No such image"),
                        pull=CommandResult(1, "", "network unreachable"))

        report = measure(inside)

        self.assertIs(report.outcome, BenchmarkOutcome.IMAGE_UNAVAILABLE)
        self.assertFalse(inside.ran_benchmark)

    def test_the_image_is_pinned_by_digest(self):
        # A moving tag would measure two Workers with different PyTorch.
        self.assertIn("@sha256:", BENCHMARK_IMAGE)
        self.assertNotIn(":latest", BENCHMARK_IMAGE)


class WhatTheContainerIsAllowedToDoTest(unittest.TestCase):
    def setUp(self):
        inside = Inside(inspect=CommandResult(0, '"id"', ""),
                        run=CommandResult(0, printed(a_result()), ""))
        measure(inside)
        self.command = [c for c in inside.commands if "--entrypoint" in c][0]
        self.text = " ".join(self.command)

    def test_it_is_thrown_away_afterwards(self):
        self.assertIn("--rm", self.command)

    def test_it_has_no_network(self):
        self.assertIn("--network", self.command)
        self.assertEqual(
            self.command[self.command.index("--network") + 1], "none")

    def test_its_filesystem_is_read_only(self):
        self.assertIn("--read-only", self.command)

    def test_it_drops_every_capability(self):
        self.assertIn("--cap-drop", self.command)
        self.assertEqual(self.command[self.command.index("--cap-drop") + 1], "ALL")

    def test_it_cannot_gain_privileges(self):
        self.assertIn("no-new-privileges", self.text)

    def test_no_host_directory_is_mounted(self):
        self.assertNotIn("-v", self.command)
        self.assertNotIn("--volume", self.command)
        self.assertNotIn("--mount", self.command)

    def test_the_only_writable_place_is_a_small_tmpfs(self):
        self.assertIn("--tmpfs", self.command)
        self.assertIn("/tmp:rw,noexec,nosuid,size=256m", self.command)

    def test_it_is_given_the_gpu(self):
        self.assertIn("--gpus", self.command)

    def test_it_runs_the_workers_own_program(self):
        # Never an image entrypoint, an argument from the Backend, or anything
        # read off this machine.
        self.assertIn("--entrypoint", self.command)
        self.assertEqual(self.command[-3:-1], [BENCHMARK_IMAGE, "-c"])

    def test_nothing_it_runs_changes_the_host(self):
        for destructive in ("apt-get", "systemctl", "nvidia-ctk", "dockerd",
                            "wsl --shutdown", "--unregister", "--privileged"):
            self.assertNotIn(destructive, self.text, destructive)


class TheWorkloadIsTheSameEverywhereTest(unittest.TestCase):
    """Adapting it to the GPU in front of us would make numbers incomparable."""

    def test_the_program_is_a_constant_with_nothing_substituted_into_it(self):
        from gshared_worker import benchmark_program

        program = benchmark_program.BENCHMARK_PROGRAM
        self.assertNotIn("{", program.split("def build_model")[0].replace(
            "{}", ""))
        self.assertIsInstance(program, str)

    def test_the_command_carries_the_program_verbatim(self):
        from gshared_worker.benchmark import _command
        from gshared_worker.benchmark_program import BENCHMARK_PROGRAM

        self.assertEqual(_command(BENCHMARK_IMAGE)[-1], BENCHMARK_PROGRAM)

    def test_the_fixed_token_count_matches_the_fixed_workload(self):
        from gshared_worker.benchmark_program import (
            BATCH_SIZE,
            SEQUENCE_LENGTH,
        )

        self.assertEqual(
            PROCESSED_TOKENS_PER_RUN, BATCH_SIZE * SEQUENCE_LENGTH * MEASURED_STEPS)

    def test_the_program_measures_with_cuda_events_not_wall_clock(self):
        from gshared_worker import benchmark_program

        program = benchmark_program.BENCHMARK_PROGRAM
        self.assertIn("torch.cuda.Event(enable_timing=True)", program)
        self.assertIn("torch.cuda.synchronize()", program)

    def test_the_program_trains_rather_than_only_running_forward(self):
        from gshared_worker import benchmark_program

        program = benchmark_program.BENCHMARK_PROGRAM
        for real_training in ("loss.backward()", "optimiser.step()",
                              "CrossEntropyLoss", "AdamW"):
            self.assertIn(real_training, program)

    def test_the_program_downloads_nothing(self):
        from gshared_worker import benchmark_program

        program = benchmark_program.BENCHMARK_PROGRAM
        for fetched in ("from_pretrained", "http", "urllib", "requests",
                        "huggingface", "download"):
            self.assertNotIn(fetched, program, fetched)

    def test_no_benchmark_score_anywhere(self):
        from gshared_worker import benchmark, benchmark_program

        with open(benchmark.__file__, encoding="utf-8") as handle:
            source = handle.read()
        for module in (benchmark_program.BENCHMARK_PROGRAM, source):
            self.assertNotIn("benchmarkScore", module)
            self.assertNotIn("throughputTflops", module)


if __name__ == "__main__":
    unittest.main()
