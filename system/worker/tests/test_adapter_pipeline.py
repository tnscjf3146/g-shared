"""A Job with an initial adapter: fetched after the dataset, handed to the container.

The pipeline's outside calls are replaced -- the context, the model, the
dataset, the pull and the container -- so what is under test is the real
``_run``: where the adapter is fetched, what a refusal does to the Job, and
that the container is told about it and nothing else changes.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gshared_worker import runtime_pipeline
from gshared_worker.adapter_fetch import AdapterRefused
from gshared_worker.command_runner import CommandResult
from gshared_worker.job_cancellation import JobTerminal
from gshared_worker.job_runtime import (
    ADAPTER_MOUNT_PATH,
    INPUT_MOUNT,
    RuntimeFailure,
    RuntimeOutcome,
    RuntimeState,
    run_command,
    run_training,
    training_arguments,
)
from gshared_worker.runtime_pipeline import RuntimePipeline, RuntimeSettings
from gshared_worker.worker_job import USER_REQUEST

from tests.test_cancel_before_running import Training, a_job, a_workspace
from tests.test_execution_context import body

IMAGE = "gshared/qlora-sft-runtime@sha256:" + "ab" * 32
ADAPTER = {"downloadUrl": "https://s/jobs/123/input/adapter.tar?sig=abc",
           "sha256": "ef" * 32, "sizeBytes": 4096}
FINISHED = RuntimeOutcome(RuntimeState.FINISHED, None, "training finished", 0)


class Harness:
    """Runs the real pipeline with every outside call recorded, not made."""

    def __init__(self, context_body, *, adapter_error=None, on_dataset=None):
        self.adapter_calls: list[dict] = []
        self.training_calls: list[dict] = []
        self.pulled = False
        self._adapter_error = adapter_error
        self._on_dataset = on_dataset
        self._body = context_body

    def run(self, terminal=None):
        settings = RuntimeSettings(
            workspace_root=Path(tempfile.mkdtemp()),
            cache_root=Path(tempfile.mkdtemp()),
            chain_id=31221, escrow_address="0xESCROW")
        pipeline = RuntimePipeline(None, "token", "worker-001", object(), settings,
                                   host_path_of=lambda path: str(path))
        harness = self

        async def no_pull(image):
            harness.pulled = True
            return None

        async def prepared_model(*args, **kwargs):
            return SimpleNamespace(path=Path(tempfile.mkdtemp()))

        def fetched_dataset(url, destination, **kwargs):
            Path(destination).write_text('{"instruction":"a","output":"b"}\n',
                                         encoding="utf-8")
            if harness._on_dataset is not None:
                harness._on_dataset()

        def fetched_adapter(url, archive_path, adapter_dir, **kwargs):
            harness.adapter_calls.append(dict(url=url, archive_path=archive_path,
                                              adapter_dir=adapter_dir, **kwargs))
            if harness._adapter_error is not None:
                raise harness._adapter_error
            Path(adapter_dir).mkdir(parents=True, exist_ok=True)

        async def trained(runner, image, workspace, model_path, training, **kwargs):
            harness.training_calls.append(dict(workspace=workspace, **kwargs))
            return FINISHED

        pipeline._pull = no_pull
        with patch.object(runtime_pipeline, "fetch_execution_context",
                          lambda *a, **k: self._body), \
                patch.object(runtime_pipeline, "check_matches_assignment",
                             lambda *a, **k: None), \
                patch.object(runtime_pipeline, "check_matches_trusted_escrow",
                             lambda *a, **k: None), \
                patch.object(runtime_pipeline, "check_image",
                             lambda ref, digest: SimpleNamespace(reference=IMAGE)), \
                patch.object(runtime_pipeline, "prepare_model", prepared_model), \
                patch.object(runtime_pipeline, "fetch_dataset", fetched_dataset), \
                patch.object(runtime_pipeline, "fetch_adapter", fetched_adapter), \
                patch.object(runtime_pipeline, "run_training", trained):
            return pipeline.run(a_job(), terminal=terminal)


class TestAJobWithAnAdapter(unittest.TestCase):
    def setUp(self):
        self.harness = Harness(body(adapter=dict(ADAPTER)))
        self.outcome = self.harness.run()

    def test_it_trains(self):
        self.assertIs(self.outcome.state, RuntimeState.FINISHED)

    def test_the_adapter_is_fetched_once_with_the_contexts_hash_and_size(self):
        self.assertEqual(len(self.harness.adapter_calls), 1)
        call = self.harness.adapter_calls[0]
        self.assertEqual(call["url"], ADAPTER["downloadUrl"])
        self.assertEqual(call["expected_sha256"], "ef" * 32)
        self.assertEqual(call["expected_size"], 4096)

    def test_it_is_unpacked_into_the_mounted_input_and_downloaded_outside_it(self):
        call = self.harness.adapter_calls[0]
        workspace = self.harness.training_calls[0]["workspace"]
        self.assertEqual(call["adapter_dir"], workspace.adapter_dir)
        self.assertEqual(call["archive_path"], workspace.adapter_archive_path)
        self.assertNotEqual(call["archive_path"].parent, workspace.input_dir)

    def test_the_container_is_told_to_continue_from_it(self):
        self.assertIs(self.harness.training_calls[0]["adapter"], True)


class TestAJobWithoutOne(unittest.TestCase):
    def setUp(self):
        self.harness = Harness(body())
        self.outcome = self.harness.run()

    def test_nothing_is_fetched_and_training_starts_fresh_as_before(self):
        self.assertIs(self.outcome.state, RuntimeState.FINISHED)
        self.assertEqual(self.harness.adapter_calls, [])
        self.assertIs(self.harness.training_calls[0]["adapter"], False)


class TestAnAdapterThatCannotBeUsed(unittest.TestCase):
    def test_a_refusal_fails_the_job_before_the_image_or_the_gpu(self):
        for error in (AdapterRefused("the adapter does not match the SHA-256"),
                      AdapterRefused("the download did not finish", fatal=False),
                      PermissionError("disk")):
            with self.subTest(error=type(error).__name__):
                harness = Harness(body(adapter=dict(ADAPTER)), adapter_error=error)
                outcome = harness.run()

                self.assertIs(outcome.state, RuntimeState.FAILED)
                self.assertIs(outcome.failure, RuntimeFailure.ADAPTER_UNAVAILABLE)
                self.assertFalse(harness.pulled)
                self.assertEqual(harness.training_calls, [])

    def test_the_failure_code_is_its_own(self):
        self.assertEqual(RuntimeFailure.ADAPTER_UNAVAILABLE.value, "ADAPTER_UNAVAILABLE")


class TestACancellationBeforeTheAdapter(unittest.TestCase):
    def test_it_stops_without_fetching(self):
        terminal = JobTerminal("123", 1)
        harness = Harness(body(adapter=dict(ADAPTER)),
                          on_dataset=lambda: terminal.request_cancel(USER_REQUEST))

        outcome = harness.run(terminal=terminal)

        self.assertIs(outcome.state, RuntimeState.CANCELLED)
        self.assertIn("before fetching the adapter", outcome.detail)
        self.assertEqual(harness.adapter_calls, [])
        self.assertEqual(harness.training_calls, [])


class TestTheContainerCommand(unittest.TestCase):
    def test_the_adapter_path_is_fixed_and_under_the_read_only_input(self):
        self.assertEqual(ADAPTER_MOUNT_PATH, f"{INPUT_MOUNT}/adapter")

        arguments = training_arguments(Training(), adapter=True)
        at = arguments.index("--adapter-path")
        self.assertEqual(arguments[at + 1], "/job/input/adapter")

    def test_without_an_adapter_the_command_is_what_it_was(self):
        self.assertNotIn("--adapter-path", training_arguments(Training()))
        self.assertEqual(training_arguments(Training(), adapter=False),
                         training_arguments(Training()))

    def test_no_extra_mount_is_added(self):
        workspace = a_workspace()
        plain = run_command(IMAGE, workspace, Path("/model"), Training())
        with_adapter = run_command(IMAGE, workspace, Path("/model"), Training(),
                                   adapter=True)

        self.assertEqual(plain.count("-v"), with_adapter.count("-v"))
        self.assertEqual(with_adapter[:len(plain)], plain)
        self.assertEqual(with_adapter[len(plain):],
                         ["--adapter-path", "/job/input/adapter"])

    def test_run_training_passes_it_through_to_docker(self):
        seen: list[list[str]] = []

        class Runner:
            async def run_streaming(self, args, *, timeout, max_output_bytes,
                                    on_stdout_line=None, on_stderr_line=None):
                seen.append(list(args))
                return CommandResult(0, "", "")

        asyncio.run(run_training(Runner(), IMAGE, a_workspace(), Path("/model"),
                                 Training(), host_path_of=str, adapter=True))

        self.assertIn("--adapter-path", seen[0])


if __name__ == "__main__":
    unittest.main()
