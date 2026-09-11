"""Cancelling a Job that has not started training yet.

A cancellation does not politely wait for the GPU to be busy. Between
JOB_ACCEPTED and ``docker run`` there is an execution context to fetch, a
model to prepare, a dataset to download and an image to pull -- minutes of it
on a cold machine -- and the customer can press cancel at any point.

Two things have to hold at every one of those points:

* **no container is started afterwards.** Answering a cancellation by
  beginning the training is the worst possible reading of it;
* **nothing is reported as having run.** ``actualRuntimeSec`` is the duration
  of a container, and a Job cancelled before one existed has no duration. A
  zero would be a measurement nobody made, and the Scheduler trains on these
  numbers.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from gshared_worker.command_runner import CommandResult
from gshared_worker.job_cancellation import JobTerminal, may_start_runtime
from gshared_worker.job_runtime import RuntimeOutcome, RuntimeState, run_training
from gshared_worker.job_workspace import JobWorkspace
from gshared_worker.runtime_pipeline import RuntimePipeline, RuntimeSettings
from gshared_worker.worker_job import USER_REQUEST, read_assignment

IMAGE = "gshared/qlora-sft-runtime@sha256:" + "ab" * 32


def a_job(job_id="123"):
    return read_assignment({
        "type": "JOB_ASSIGN", "jobId": job_id,
        "timestamp": "2026-09-11T01:00:00Z",
        "payload": {
            "dockerImageId": 1, "modelId": 3, "datasetId": 42,
            "trainingMode": "AUTO",
            "trainingConfig": {"epochs": 3, "batchSize": 4,
                               "learningRate": 0.0002, "maxLength": 512},
            "escrowJobId": "17", "externalRef": "0x" + "ab" * 32,
            "price": "9000000000000000000",
            "clientWallet": "0x1111111111111111111111111111111111111111",
            "providerWallet": "0x2222222222222222222222222222222222222222",
        },
    })


class Phase:
    """Names the pipeline phases, in the order they happen."""

    CONTEXT = "context"
    IMAGE = "image"
    MODEL = "model"
    DATASET = "dataset"
    PULL = "pull"
    RUN = "run"


class Pipeline(RuntimePipeline):
    """The real pipeline with its four outside calls replaced.

    Subclassed rather than mocked at module level so the checkpoints under
    test -- which live in ``_run`` -- are the real ones.
    """

    def __init__(self, cancel_at=None, terminal=None):
        settings = RuntimeSettings(
            workspace_root=Path(tempfile.mkdtemp()),
            cache_root=Path(tempfile.mkdtemp()),
            chain_id=31337, escrow_address="0x" + "11" * 20)
        super().__init__(None, "token", "worker-001", Runner(), settings,
                         host_path_of=lambda path: str(path))
        self.reached: list[str] = []
        self._cancel_at = cancel_at
        self._terminal = terminal

    def _maybe_cancel(self, phase):
        self.reached.append(phase)
        if self._cancel_at == phase and self._terminal is not None:
            self._terminal.request_cancel(USER_REQUEST)


class Runner:
    async def run(self, args, *, timeout, max_output_bytes):
        return CommandResult(0, '["' + IMAGE + '"]', "")

    async def run_streaming(self, args, *, timeout, max_output_bytes,
                            on_stdout_line=None, on_stderr_line=None):
        return CommandResult(0, "", "")


def a_workspace() -> JobWorkspace:
    workspace = JobWorkspace.under(Path(tempfile.mkdtemp()), "123").prepare()
    for name in ("adapter_config.json", "adapter_model.safetensors",
                 "training_metrics.json"):
        (workspace.output_dir / name).write_bytes(b"x")
    return workspace


class Training:
    epochs, batch_size, learning_rate, max_length = 1, 4, 0.0002, 512


class TestTheGateBeforeTheContainer(unittest.TestCase):
    """``may_start_runtime`` -- the check in the moment before ``docker run``."""

    def test_a_job_nobody_cancelled_may_run(self):
        self.assertTrue(may_start_runtime(JobTerminal("123")))

    def test_a_cancelled_one_may_not(self):
        terminal = JobTerminal("123")
        terminal.request_cancel(USER_REQUEST)

        self.assertFalse(may_start_runtime(terminal))

    def test_the_answer_changes_the_instant_the_request_lands(self):
        # The race this closes: prepared, about to run, cancel arrives.
        terminal = JobTerminal("123")
        self.assertTrue(may_start_runtime(terminal))

        terminal.request_cancel(USER_REQUEST)

        self.assertFalse(may_start_runtime(terminal))


class TestThePipelineStopsAtItsCheckpoints(unittest.TestCase):
    def run_with(self, cancel_before_start=True):
        terminal = JobTerminal("123", 1)
        if cancel_before_start:
            terminal.request_cancel(USER_REQUEST)
        pipeline = Pipeline(terminal=terminal)
        return pipeline.run(a_job(), terminal=terminal), terminal

    def test_a_job_cancelled_before_anything_never_reaches_the_backend(self):
        outcome, _ = self.run_with()

        self.assertIs(outcome.state, RuntimeState.CANCELLED)

    def test_it_is_not_reported_as_a_failure(self):
        # A cancelled Job did not fail. Saying so would put a fault in the
        # customer's record for something they asked for.
        outcome, _ = self.run_with()

        self.assertIsNone(outcome.failure)
        self.assertIs(outcome.state, RuntimeState.CANCELLED)

    def test_nothing_ran_so_nothing_is_timed(self):
        outcome, _ = self.run_with()

        self.assertIsNone(outcome.actual_runtime_sec)

    def test_no_container_was_ever_named(self):
        _, terminal = self.run_with()

        self.assertIsNone(terminal.handle)

    def test_the_detail_says_it_was_the_customer(self):
        outcome, _ = self.run_with()

        self.assertIn("customer", outcome.detail)


class TestTheRuntimeItselfKnowsItWasStopped(unittest.TestCase):
    def train(self, exit_code, cancelled, clock=None):
        workspace = a_workspace()

        class Stopping:
            async def run_streaming(self, command, *, timeout,
                                    max_output_bytes, on_stdout_line=None,
                                    on_stderr_line=None):
                return CommandResult(exit_code, "", "")

        return asyncio.run(run_training(
            Stopping(), IMAGE, workspace, workspace.root, Training(),
            cancelled=cancelled, monotonic=clock))

    def test_a_container_killed_by_a_stop_is_cancelled_not_failed(self):
        """``docker stop`` exits 137 or 143.

        The exit table does not know those codes and would read them as
        UNKNOWN_RUNTIME_EXIT -- reporting a crash for a Job the customer
        asked to stop.
        """
        for code in (137, 143):
            with self.subTest(exit_code=code):
                outcome = self.train(code, cancelled=lambda: True)

                self.assertIs(outcome.state, RuntimeState.CANCELLED)
                self.assertIsNone(outcome.failure)

    def test_the_same_exit_without_a_cancellation_is_still_a_failure(self):
        outcome = self.train(137, cancelled=lambda: False)

        self.assertIs(outcome.state, RuntimeState.FAILED)
        self.assertIsNotNone(outcome.failure)

    def test_a_run_that_finished_first_is_still_a_success(self):
        """It really did finish. Who owns the ending is decided elsewhere.

        Calling a completed run cancelled here would throw away a result that
        exists -- and the chain, not this function, is what settles the race.
        """
        outcome = self.train(0, cancelled=lambda: True)

        self.assertIs(outcome.state, RuntimeState.FINISHED)

    def test_with_no_cancellation_configured_nothing_changes(self):
        outcome = self.train(13, cancelled=None)

        self.assertIs(outcome.state, RuntimeState.FAILED)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class TestHowLongACancelledJobRan(unittest.TestCase):
    """From the container starting to the container being gone.

    Not from the stop being requested -- the Job was using the GPU for the
    whole of the shutdown too, and the supplier's machine was busy for it.
    """

    def test_it_is_measured_to_the_process_exit(self):
        clock = Clock()
        workspace = a_workspace()

        class Stopping:
            async def run_streaming(self, command, *, timeout,
                                    max_output_bytes, on_stdout_line=None,
                                    on_stderr_line=None):
                clock.advance(40.0)          # training
                # The stop is requested here, at t+40 ...
                clock.advance(2.5)           # ... and it takes this long
                return CommandResult(137, "", "")

        outcome = asyncio.run(run_training(
            Stopping(), IMAGE, workspace, workspace.root, Training(),
            cancelled=lambda: True, monotonic=clock))

        self.assertEqual(outcome.actual_runtime_sec, 42.5)

    def test_preparation_before_it_is_not_counted(self):
        clock = Clock()
        clock.advance(3600.0)                 # a cold machine
        workspace = a_workspace()

        class Stopping:
            async def run_streaming(self, command, *, timeout,
                                    max_output_bytes, on_stdout_line=None,
                                    on_stderr_line=None):
                clock.advance(42.5)
                return CommandResult(143, "", "")

        outcome = asyncio.run(run_training(
            Stopping(), IMAGE, workspace, workspace.root, Training(),
            cancelled=lambda: True, monotonic=clock))

        self.assertEqual(outcome.actual_runtime_sec, 42.5)

    def test_a_cancellation_before_the_container_reports_nothing(self):
        from gshared_worker.runtime_pipeline import _cancelled_before_starting

        outcome = _cancelled_before_starting("before the dataset")

        self.assertIsNone(outcome.actual_runtime_sec)
        self.assertIs(outcome.state, RuntimeState.CANCELLED)

    def test_it_is_never_negative(self):
        clock = Clock()
        workspace = a_workspace()

        class Stopping:
            async def run_streaming(self, command, *, timeout,
                                    max_output_bytes, on_stdout_line=None,
                                    on_stderr_line=None):
                clock.now = -100.0           # a clock that went sideways
                return CommandResult(137, "", "")

        outcome = asyncio.run(run_training(
            Stopping(), IMAGE, workspace, workspace.root, Training(),
            cancelled=lambda: True, monotonic=clock))

        self.assertGreaterEqual(outcome.actual_runtime_sec, 0.0)


class TestTheContainerIsIdentifiedWhenItStarts(unittest.TestCase):
    def test_the_handle_arrives_as_the_command_goes_out(self):
        """A cancellation a millisecond later needs something to stop."""
        terminal = JobTerminal("123", 4)
        workspace = a_workspace()
        seen: list = []

        class Watching:
            async def run_streaming(self, command, *, timeout,
                                    max_output_bytes, on_stdout_line=None,
                                    on_stderr_line=None):
                seen.append(terminal.handle)
                return CommandResult(0, "", "")

        def started(name):
            from gshared_worker.runtime_stop import RuntimeHandle

            terminal.container_started(
                RuntimeHandle("123", 4, name))

        asyncio.run(run_training(
            Watching(), IMAGE, workspace, workspace.root, Training(),
            on_started=started))

        self.assertIsNotNone(seen[0], "the container ran before it was named")
        self.assertEqual(seen[0].container_name, "gshared-job-123")
        self.assertEqual(seen[0].generation, 4)

    def test_the_command_carries_the_labels_that_prove_it(self):
        from gshared_worker.job_runtime import (
            LABEL_GENERATION,
            LABEL_JOB_ID,
            run_command,
        )

        command = run_command(IMAGE, a_workspace(), Path("/model"), Training(),
                              generation=4)

        self.assertIn(f"{LABEL_JOB_ID}=123", command)
        self.assertIn(f"{LABEL_GENERATION}=4", command)

    def test_the_labels_carry_nothing_private(self):
        from gshared_worker.job_runtime import run_command

        command = " ".join(run_command(
            IMAGE, a_workspace(), Path("/model"), Training(), generation=4))

        for forbidden in ("token", "wallet", "0x", "secret", "key="):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, command.replace(IMAGE, ""))


if __name__ == "__main__":
    unittest.main()
