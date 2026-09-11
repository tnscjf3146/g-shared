"""The container that finally uses the GPU: how it is started and what it did.

Two subjects. The first is the ``docker run`` command, and the tests on it are
blunt on purpose -- each flag is a thing that stops being true the moment
somebody edits the list, and a container holding a customer's dataset on a
stranger's PC is not a good place to find that out.

The second is reading the result: the exit code the image publishes, and the
events it prints. Neither is sent anywhere yet. Task 8 turns the events into
JOB_PROGRESS and task 9 turns the outcome into JOB_RESULT or JOB_FAILED; this
stage stops at a value.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import tempfile
import unittest
from pathlib import Path

from gshared_worker.job_runtime import (
    CACHE_MOUNT,
    EXIT_CODES,
    INPUT_MOUNT,
    MODEL_MOUNT,
    OUTPUT_MOUNT,
    RUNTIME_UID,
    RuntimeFailure,
    RuntimeState,
    container_name,
    failure_for_exit,
    parse_events,
    run_command,
    run_training,
    training_arguments,
)
from gshared_worker.job_workspace import JobWorkspace, UnsafePath

IMAGE = "gshared/qlora-sft-runtime@sha256:" + "ab" * 32


class Training:
    epochs = 1
    batch_size = 16
    learning_rate = 0.0002
    max_length = 512


def a_workspace(job_id="123") -> JobWorkspace:
    return JobWorkspace.under(Path(tempfile.mkdtemp()), job_id).prepare()


def a_command(job_id="123", training=None) -> list[str]:
    return run_command(IMAGE, a_workspace(job_id), Path("/models/qwen"),
                       training or Training())


def code_only(module) -> str:
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            body.pop(0)
            if not body:
                body.append(ast.Pass())
    return ast.unparse(tree)


class TestTheContainerIsLockedDown(unittest.TestCase):
    """Each of these is a flag that quietly stops being there one day."""

    def setUp(self):
        self.command = a_command()
        self.joined = " ".join(self.command)

    def test_it_gets_the_gpu(self):
        self.assertIn("--gpus", self.command)
        self.assertIn("all", self.command)

    def test_it_has_no_network(self):
        # It holds a customer's dataset.
        self.assertIn("--network", self.command)
        self.assertIn("none", self.command)

    def test_the_root_filesystem_is_read_only(self):
        self.assertIn("--read-only", self.command)

    def test_every_capability_is_dropped(self):
        self.assertIn("--cap-drop", self.command)
        self.assertIn("ALL", self.command)

    def test_it_cannot_gain_privileges(self):
        self.assertIn("no-new-privileges", self.command)

    def test_processes_are_limited(self):
        self.assertIn("--pids-limit", self.command)

    def test_memory_is_limited(self):
        self.assertIn("--memory", self.command)

    def test_it_runs_as_a_non_root_user(self):
        self.assertIn("--user", self.command)
        self.assertIn(f"{RUNTIME_UID}:{RUNTIME_UID}", self.joined)

    def test_it_is_removed_when_it_finishes(self):
        self.assertIn("--rm", self.command)

    def test_the_command_is_a_list_of_arguments(self):
        # Never a shell string. `learningRate` comes from the Backend.
        self.assertIsInstance(self.command, list)
        self.assertTrue(all(isinstance(part, str) for part in self.command))

    def test_nothing_in_this_module_uses_a_shell(self):
        from gshared_worker import job_runtime

        source = code_only(job_runtime)
        for absent in ("shell=True", "os.system", "subprocess"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)


class TestTheMounts(unittest.TestCase):
    def setUp(self):
        self.command = a_command()
        self.mounts = [self.command[i + 1] for i, part in enumerate(self.command)
                       if part == "-v"]
        self.tmpfs = [self.command[i + 1] for i, part in enumerate(self.command)
                      if part == "--tmpfs"]

    def test_the_model_is_read_only(self):
        model = [m for m in self.mounts if m.endswith(f":{MODEL_MOUNT}:ro")]

        self.assertEqual(len(model), 1, self.mounts)

    def test_the_input_is_read_only(self):
        # The dataset was hashed before it was mounted. A container that could
        # rewrite it would make that check meaningless.
        found = [m for m in self.mounts if m.endswith(f":{INPUT_MOUNT}:ro")]

        self.assertEqual(len(found), 1, self.mounts)

    def test_the_output_is_writable(self):
        found = [m for m in self.mounts if m.endswith(f":{OUTPUT_MOUNT}")]

        self.assertEqual(len(found), 1, self.mounts)

    def test_nothing_else_is_mounted(self):
        self.assertEqual(len(self.mounts), 3, self.mounts)

    def test_the_docker_socket_is_never_mounted(self):
        # It would be a complete escape from the container.
        self.assertNotIn("docker.sock", " ".join(self.mounts))

    def test_no_host_root_is_mounted(self):
        for mount in self.mounts:
            with self.subTest(mount=mount):
                self.assertFalse(mount.startswith("/:"))
                self.assertFalse(mount.startswith("C:\\:"))

    def test_the_cache_tmpfs_names_the_runtime_user(self):
        """Found the hard way in the QLoRA smoke tests.

        A bare ``--tmpfs`` mounts a root-owned filesystem over the directory
        the image chowned at build time, and the non-root runtime then cannot
        write its own cache -- Torch Inductor fails with PermissionError.
        """
        cache = [t for t in self.tmpfs if t.startswith(CACHE_MOUNT)]

        self.assertEqual(len(cache), 1, self.tmpfs)
        self.assertIn(f"uid={RUNTIME_UID}", cache[0])
        self.assertIn(f"gid={RUNTIME_UID}", cache[0])

    def test_tmp_is_a_tmpfs_owned_by_the_runtime_user(self):
        tmp = [t for t in self.tmpfs if t.startswith("/tmp")]

        self.assertEqual(len(tmp), 1, self.tmpfs)
        self.assertIn(f"uid={RUNTIME_UID}", tmp[0])


class TestNoSecretReachesTheContainer(unittest.TestCase):
    """It trains a model and writes files. It needs none of these."""

    def test_no_environment_variable_carries_a_credential(self):
        command = a_command()
        environment = [command[i + 1] for i, part in enumerate(command)
                       if part == "-e"]

        self.assertEqual(environment, [], environment)

    def test_nothing_in_the_command_looks_like_a_secret(self):
        joined = " ".join(a_command()).lower()

        for absent in ("token", "privatekey", "private_key", "password",
                       "secret", "bearer", "authorization", "registry"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, joined)

    def test_the_module_never_reaches_for_one(self):
        from gshared_worker import job_runtime

        source = code_only(job_runtime)
        for absent in ("worker_token", "private_key", "operator_address",
                       "Authorization", "workerToken"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)


class TestTheTrainingCommandLine(unittest.TestCase):
    def test_the_seven_arguments_the_runtime_expects(self):
        arguments = training_arguments(Training())

        for flag in ("--model-path", "--dataset-path", "--output-dir",
                     "--epochs", "--batch-size", "--learning-rate",
                     "--max-length"):
            with self.subTest(flag=flag):
                self.assertIn(flag, arguments)

    def test_the_paths_are_the_mount_points(self):
        arguments = training_arguments(Training())

        self.assertEqual(arguments[arguments.index("--model-path") + 1],
                         MODEL_MOUNT)
        self.assertEqual(arguments[arguments.index("--output-dir") + 1],
                         OUTPUT_MOUNT)
        self.assertTrue(arguments[arguments.index("--dataset-path") + 1]
                        .startswith(INPUT_MOUNT))

    def test_the_numbers_come_from_the_context(self):
        class Other:
            epochs = 3
            batch_size = 8
            learning_rate = 0.0005
            max_length = 1024

        arguments = training_arguments(Other())

        self.assertEqual(arguments[arguments.index("--epochs") + 1], "3")
        self.assertEqual(arguments[arguments.index("--batch-size") + 1], "8")
        self.assertEqual(arguments[arguments.index("--max-length") + 1], "1024")

    def test_the_learning_rate_survives_the_round_trip(self):
        # repr(), not str(): a rate that arrived as 2e-05 must not reach the
        # container as "2e-05" rounded to zero by some formatter.
        arguments = training_arguments(Training())
        value = arguments[arguments.index("--learning-rate") + 1]

        self.assertEqual(float(value), 0.0002)

    def test_no_policy_name_is_ever_passed(self):
        """The container has never heard of SPEED or BALANCED.

        Those are the AI Scheduler's names for sets of numbers, resolved long
        before this point.
        """
        joined = " ".join(a_command())

        for absent in ("SPEED", "BALANCED", "TRAINING", "AUTO", "CUSTOM"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, joined)

    def test_a_zero_epoch_count_is_refused(self):
        class Bad(Training):
            epochs = 0

        with self.assertRaises(ValueError):
            training_arguments(Bad())

    def test_a_non_finite_learning_rate_is_refused(self):
        class Bad(Training):
            learning_rate = float("inf")

        with self.assertRaises(ValueError):
            training_arguments(Bad())

    def test_a_boolean_batch_size_is_refused(self):
        # True is an int in Python and would sail through as a batch of one.
        class Bad(Training):
            batch_size = True

        with self.assertRaises(ValueError):
            training_arguments(Bad())


class TestTheContainerName(unittest.TestCase):
    def test_it_is_derived_from_the_job(self):
        self.assertEqual(container_name("123"), "gshared-job-123")

    def test_a_job_id_that_is_not_a_number_is_refused(self):
        for bad in ("../../etc", "12;rm -rf /", "a b", "", "12/34"):
            with self.subTest(bad=bad):
                with self.assertRaises(UnsafePath):
                    container_name(bad)


class TestReadingTheExitCode(unittest.TestCase):
    """The image publishes these. This package does not get to reinterpret them."""

    def test_the_published_codes(self):
        self.assertEqual(EXIT_CODES[10], RuntimeFailure.INVALID_INPUT)
        self.assertEqual(EXIT_CODES[11], RuntimeFailure.MODEL_LOAD_FAILED)
        self.assertEqual(EXIT_CODES[12], RuntimeFailure.CUDA_UNAVAILABLE)
        self.assertEqual(EXIT_CODES[13], RuntimeFailure.CUDA_OOM)
        self.assertEqual(EXIT_CODES[14], RuntimeFailure.TRAINING_FAILED)
        self.assertEqual(EXIT_CODES[15], RuntimeFailure.OUTPUT_VALIDATION_FAILED)

    def test_zero_is_not_in_the_failure_table(self):
        self.assertNotIn(0, EXIT_CODES)

    def test_an_unknown_code_is_still_a_failure(self):
        # 137 is a kill, 143 a term, 1 and 255 anything at all. None of them
        # are success.
        for code in (1, 137, 143, 255, 99):
            with self.subTest(code=code):
                self.assertIs(failure_for_exit(code),
                              RuntimeFailure.UNKNOWN_RUNTIME_EXIT)

    def test_no_answer_at_all_is_a_timeout(self):
        self.assertIs(failure_for_exit(None), RuntimeFailure.TIMED_OUT)


class TestReadingTheEvents(unittest.TestCase):
    def test_a_marked_line_is_parsed(self):
        line = 'GSHARED_EVENT={"event":"step","step":7,"totalSteps":100}'

        events = parse_events(line)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].name, "step")
        self.assertEqual(events[0].step, 7)

    def test_the_four_events_the_runtime_emits(self):
        stdout = "\n".join(
            f'GSHARED_EVENT={{"event":"{name}"}}'
            for name in ("training_started", "step", "training_completed",
                         "failed"))

        self.assertEqual([e.name for e in parse_events(stdout)],
                         ["training_started", "step", "training_completed",
                          "failed"])

    def test_progress_is_step_over_total(self):
        line = 'GSHARED_EVENT={"event":"step","step":25,"totalSteps":100}'

        self.assertAlmostEqual(parse_events(line)[0].progress_percent, 25.0)

    def test_progress_is_none_without_a_total(self):
        line = 'GSHARED_EVENT={"event":"step","step":25}'

        self.assertIsNone(parse_events(line)[0].progress_percent)

    def test_other_output_is_ignored(self):
        """stdout is shared with PyTorch, bitsandbytes and CUDA.

        A run that finished must not be reported as failed because a warning
        happened to sit next to a marker.
        """
        stdout = "\n".join([
            "Loading weights: 100%",
            'GSHARED_EVENT={"event":"step","step":1,"totalSteps":2}',
            "some warning about a deprecated flag",
        ])

        self.assertEqual(len(parse_events(stdout)), 1)

    def test_a_malformed_event_is_skipped_rather_than_raised_on(self):
        stdout = "\n".join([
            "GSHARED_EVENT={not json",
            'GSHARED_EVENT={"event":"step","step":1}',
        ])

        self.assertEqual(len(parse_events(stdout)), 1)

    def test_an_event_that_is_not_an_object_is_skipped(self):
        self.assertEqual(parse_events('GSHARED_EVENT=[1,2,3]'), [])

    def test_an_event_with_no_name_is_skipped(self):
        self.assertEqual(parse_events('GSHARED_EVENT={"step":1}'), [])

    def test_empty_output_is_no_events(self):
        self.assertEqual(parse_events(""), [])
        self.assertEqual(parse_events(None), [])


class Ran:
    """A fake command runner with one canned result."""

    def __init__(self, exit_code=0, stdout="", raises=None, result=True):
        self._exit = exit_code
        self._stdout = stdout
        self._raises = raises
        self._result = result
        self.commands: list = []

    async def run(self, args, *, timeout, max_output_bytes=0):
        self.commands.append(args)
        if self._raises:
            raise self._raises
        if not self._result:
            return None

        class Result:
            exit_code = self._exit
            stdout = self._stdout
            stderr = ""

        return Result()


def wrote_output(workspace: JobWorkspace) -> JobWorkspace:
    (workspace.output_dir / "adapter_config.json").write_text("{}", encoding="utf-8")
    (workspace.output_dir / "adapter_model.safetensors").write_bytes(b"")
    return workspace


class TestRunningIt(unittest.TestCase):
    def run_it(self, runner, workspace=None):
        workspace = workspace or a_workspace()
        return asyncio.run(run_training(
            runner, IMAGE, workspace, Path("/models/qwen"), Training()))

    def test_exit_zero_with_an_adapter_is_success(self):
        workspace = wrote_output(a_workspace())

        outcome = self.run_it(Ran(0), workspace)

        self.assertIs(outcome.state, RuntimeState.FINISHED)
        self.assertTrue(outcome.succeeded)

    def test_exit_zero_with_no_adapter_is_not_success(self):
        # The container checks this itself and exits 15. Reaching here means
        # its own check did not run, and an empty directory is not a result.
        outcome = self.run_it(Ran(0))

        self.assertFalse(outcome.succeeded)
        self.assertIs(outcome.failure, RuntimeFailure.OUTPUT_VALIDATION_FAILED)

    def test_a_failing_exit_code_is_mapped(self):
        outcome = self.run_it(Ran(13))

        self.assertIs(outcome.failure, RuntimeFailure.CUDA_OOM)
        self.assertEqual(outcome.exit_code, 13)

    def test_the_containers_own_message_is_used_when_it_left_one(self):
        stdout = ('GSHARED_EVENT={"event":"failed","errorCode":"CUDA_OOM",'
                  '"message":"the GPU ran out of memory during training"}')

        outcome = self.run_it(Ran(13, stdout))

        self.assertIn("ran out of memory", outcome.detail)

    def test_events_are_kept_for_the_next_stage(self):
        stdout = 'GSHARED_EVENT={"event":"step","step":1,"totalSteps":2}'
        workspace = wrote_output(a_workspace())

        outcome = self.run_it(Ran(0, stdout), workspace)

        self.assertEqual(len(outcome.events), 1)
        self.assertEqual(outcome.last_event.name, "step")

    def test_a_docker_that_did_not_answer_is_a_timeout(self):
        outcome = self.run_it(Ran(result=False))

        self.assertIs(outcome.failure, RuntimeFailure.TIMED_OUT)

    def test_a_docker_that_could_not_be_started(self):
        outcome = self.run_it(Ran(raises=OSError("no such file")))

        self.assertIs(outcome.failure, RuntimeFailure.DOCKER_UNAVAILABLE)

    def test_the_output_directory_survives_a_failure(self):
        # Task 9 needs whatever is there, and a pipeline that tidied up would
        # leave nothing to look at.
        workspace = a_workspace()

        self.run_it(Ran(14), workspace)

        self.assertTrue(workspace.output_dir.is_dir())

    def test_nothing_is_sent_to_the_backend(self):
        """This stage returns a value. It does not report.

        JOB_PROGRESS is task 8 and JOB_RESULT/JOB_FAILED are task 9.
        """
        from gshared_worker import job_runtime

        source = code_only(job_runtime)
        for absent in ("JOB_PROGRESS", "JOB_RESULT", "JOB_FAILED",
                       "socket", "RegisteredSocket", ".send("):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)


if __name__ == "__main__":
    unittest.main()
