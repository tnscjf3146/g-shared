"""What ``actualRuntimeSec`` means, measured with a clock a test can move.

One authoritative definition, because the Scheduler trains on this number:

    from the moment the training container process starts, to the moment it
    has stopped and the outcome is decided.

In: container startup, loading the model inside it, the training itself, the
container's own output check, termination.

Out: the execution context, preparing the weights, downloading the dataset,
pulling the image -- all of which happen before -- and packaging, uploading
and the chain, all of which happen after.

The container publishes a ``runtimeSec`` of its own and it is **not** this
number. That clock starts when the Trainer does, so it leaves out the model
load, which on a cold machine is most of a short job. The Scheduler is
predicting how long a supplier's GPU will be busy, and the GPU is busy for all
of it. The container's figure stays in the metrics file as a cross-check.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from gshared_worker.command_runner import CommandResult
from gshared_worker.job_runtime import (
    RuntimeFailure,
    RuntimeState,
    run_training,
)
from gshared_worker.job_workspace import JobWorkspace

IMAGE = "gshared/qlora-sft-runtime@sha256:" + "ab" * 32


class Clock:
    """Monotonic seconds, moved only by the code under test."""

    def __init__(self) -> None:
        self.now = 0.0
        self.reads = 0

    def __call__(self) -> float:
        self.reads += 1
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Training:
    epochs, batch_size, learning_rate, max_length = 1, 4, 0.0002, 512


class Runner:
    """A container that takes ``takes`` seconds of the fake clock."""

    def __init__(self, clock, takes=0.0, exit_code=0, raises=None,
                 returns_none=False):
        self._clock = clock
        self._takes = takes
        self._exit = exit_code
        self._raises = raises
        self._none = returns_none

    async def run_streaming(self, command, *, timeout, max_output_bytes,
                            on_stdout_line=None, on_stderr_line=None):
        if self._raises is not None:
            self._clock.advance(self._takes)
            raise self._raises
        self._clock.advance(self._takes)
        if self._none:
            return None
        return CommandResult(self._exit, "", "")


def a_workspace(with_output=True) -> JobWorkspace:
    workspace = JobWorkspace.under(Path(tempfile.mkdtemp()), "123").prepare()
    if with_output:
        (workspace.output_dir / "adapter_config.json").write_bytes(b"{}")
        (workspace.output_dir / "adapter_model.safetensors").write_bytes(b"w")
        (workspace.output_dir / "training_metrics.json").write_bytes(b"{}")
    return workspace


def train(runner, clock, workspace=None) -> object:
    workspace = workspace or a_workspace()
    return asyncio.run(run_training(
        runner, IMAGE, workspace, workspace.root, Training(),
        monotonic=clock))


class TestItMeasuresTheContainer(unittest.TestCase):
    def test_a_successful_run_carries_its_duration(self):
        clock = Clock()

        outcome = train(Runner(clock, takes=982.4), clock)

        self.assertIs(outcome.state, RuntimeState.FINISHED)
        self.assertEqual(outcome.actual_runtime_sec, 982.4)

    def test_a_failed_run_carries_it_too(self):
        clock = Clock()

        outcome = train(Runner(clock, takes=41.2, exit_code=13), clock)

        self.assertIs(outcome.failure, RuntimeFailure.CUDA_OOM)
        self.assertEqual(outcome.actual_runtime_sec, 41.2)

    def test_a_run_that_timed_out_carries_how_long_it_ran(self):
        clock = Clock()

        outcome = train(Runner(clock, takes=86400.0, returns_none=True), clock)

        self.assertIs(outcome.failure, RuntimeFailure.TIMED_OUT)
        self.assertEqual(outcome.actual_runtime_sec, 86400.0)

    def test_a_container_that_never_started_still_reports_the_attempt(self):
        # Docker itself failed. Time passed, and it was spent trying to run
        # the container -- which is what the number measures.
        clock = Clock()

        outcome = train(Runner(clock, takes=3.0,
                               raises=RuntimeError("no docker")), clock)

        self.assertIs(outcome.failure, RuntimeFailure.DOCKER_UNAVAILABLE)
        self.assertEqual(outcome.actual_runtime_sec, 3.0)

    def test_an_exit_zero_with_no_adapter_is_measured_the_same(self):
        clock = Clock()
        workspace = a_workspace(with_output=False)

        outcome = train(Runner(clock, takes=7.5), clock, workspace)

        self.assertIs(outcome.failure, RuntimeFailure.OUTPUT_VALIDATION_FAILED)
        self.assertEqual(outcome.actual_runtime_sec, 7.5)


class TestWhatItLeavesOut(unittest.TestCase):
    def test_time_before_the_container_is_not_in_it(self):
        """The context, the weights, the dataset and the pull.

        All four happen in the pipeline before ``run_training`` is called, so
        the clock has not started. Here that is an hour of the fake clock
        moving before the call.
        """
        clock = Clock()
        clock.advance(3600.0)          # preparing everything

        outcome = train(Runner(clock, takes=60.0), clock)

        self.assertEqual(outcome.actual_runtime_sec, 60.0)

    def test_time_after_it_is_not_in_it_either(self):
        # Packaging, uploading and the chain all come later and none of them
        # can change a value that is already in the outcome.
        clock = Clock()
        outcome = train(Runner(clock, takes=60.0), clock)

        clock.advance(900.0)           # upload and submitResult

        self.assertEqual(outcome.actual_runtime_sec, 60.0)

    def test_the_pipeline_measures_nothing_of_its_own(self):
        """One clock, in one place.

        A second measurement in the pipeline would be a second answer to the
        question the Scheduler trains on.
        """
        import ast
        import inspect

        from gshared_worker import runtime_pipeline

        tree = ast.parse(inspect.getsource(runtime_pipeline))
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if isinstance(body, list) and body:
                first = body[0]
                if (isinstance(first, ast.Expr)
                        and isinstance(first.value, ast.Constant)
                        and isinstance(first.value.value, str)):
                    body.pop(0)
                    if not body:
                        body.append(ast.Pass())
        source = ast.unparse(tree)

        self.assertNotIn("monotonic", source)
        self.assertNotIn("time.time", source)
        self.assertNotIn("datetime", source)


class TestTheNumberIsAlwaysUsable(unittest.TestCase):
    def test_it_is_never_negative(self):
        class Backwards(Clock):
            def __call__(self):
                self.reads += 1
                # A clock that appears to go back. time.monotonic cannot,
                # but the floor is cheaper than trusting that for ever.
                return 100.0 if self.reads == 1 else 40.0

        clock = Backwards()
        outcome = train(Runner(clock), clock)

        self.assertGreaterEqual(outcome.actual_runtime_sec, 0.0)

    def test_it_is_finite(self):
        import math

        clock = Clock()
        outcome = train(Runner(clock, takes=12.25), clock)

        self.assertTrue(math.isfinite(outcome.actual_runtime_sec))

    def test_a_zero_length_run_is_zero_not_none(self):
        clock = Clock()

        outcome = train(Runner(clock, takes=0.0), clock)

        self.assertEqual(outcome.actual_runtime_sec, 0.0)

    def test_a_failure_before_any_container_has_none_not_zero(self):
        """A measurement that was never made is not a measurement of nothing.

        The pipeline's early refusals -- a context that did not match, a
        dataset that would not download -- build their outcome without ever
        reaching the container, and the Backend's column is nullable.
        """
        from gshared_worker.runtime_pipeline import _failed

        outcome = _failed(RuntimeFailure.DATASET_UNAVAILABLE,
                          "the dataset could not be downloaded")

        self.assertIsNone(outcome.actual_runtime_sec)

    def test_the_clock_is_monotonic_in_the_real_code(self):
        import ast
        import inspect

        from gshared_worker import job_runtime

        tree = ast.parse(inspect.getsource(job_runtime))
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if isinstance(body, list) and body:
                first = body[0]
                if (isinstance(first, ast.Expr)
                        and isinstance(first.value, ast.Constant)
                        and isinstance(first.value.value, str)):
                    body.pop(0)
                    if not body:
                        body.append(ast.Pass())
        source = ast.unparse(tree)

        self.assertIn("time.monotonic", source)
        self.assertNotIn("time.time()", source)
        self.assertNotIn("datetime", source)


class TestItIsNotTheContainersOwnFigure(unittest.TestCase):
    def test_they_are_allowed_to_differ(self):
        """The container's clock starts later, so its number is smaller.

        Reported separately on purpose: ``training_metrics.json`` keeps the
        Trainer's view for whoever is diagnosing a slow run, and the Backend
        gets the one the Scheduler predicts.
        """
        clock = Clock()

        class Says(Runner):
            async def run_streaming(self, command, *, timeout,
                                    max_output_bytes, on_stdout_line=None,
                                    on_stderr_line=None):
                on_stdout_line('GSHARED_EVENT={"event":"training_started",'
                               '"totalSteps":4}')
                self._clock.advance(120.0)
                on_stdout_line('GSHARED_EVENT={"event":"training_completed",'
                               '"runtimeSec":95.0,"totalSteps":4}')
                return CommandResult(0, "", "")

        outcome = train(Says(clock), clock)

        self.assertEqual(outcome.actual_runtime_sec, 120.0)
        said = [e for e in outcome.events if e.name == "training_completed"]
        self.assertEqual(said[0].payload["runtimeSec"], 95.0)
        self.assertNotEqual(outcome.actual_runtime_sec,
                            said[0].payload["runtimeSec"])


if __name__ == "__main__":
    unittest.main()
