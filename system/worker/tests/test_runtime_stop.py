"""Stopping the container -- ours, and only ours, and knowing it stopped.

Two failures are being designed against, and they are opposites.

**Stopping somebody else's container.** The name is derived from the job id,
so a container left by an earlier run of the same Job, or started by another
Worker process on this machine, wears it too. ``docker stop`` by name would
kill whichever one is wearing it.

**Believing a container stopped when it did not.** The answer this module
returns is what the escrow decides on. An ``abortJob`` sent while the GPU is
still training refunds the customer and leaves the machine doing the work
anyway -- the one outcome that costs the supplier money for nothing.
"""

from __future__ import annotations

import asyncio
import json
import unittest

from gshared_worker.command_runner import CommandResult
from gshared_worker.job_runtime import LABEL_GENERATION, LABEL_JOB_ID
from gshared_worker.runtime_stop import (
    FIELD_SEPARATOR,
    STOP_GRACE_SECONDS,
    ContainerState,
    RuntimeHandle,
    StopOutcome,
    inspect_command,
    look_at_container,
    read_state,
    stop_command,
    stop_container,
)

JOB = "123"


def a_handle(job_id=JOB, generation=1) -> RuntimeHandle:
    return RuntimeHandle.for_job(job_id, generation)


def inspected(running=True, job_id=JOB, generation=1, labels=None) -> str:
    if labels is None:
        labels = {LABEL_JOB_ID: job_id, LABEL_GENERATION: str(generation)}
    return f"{'true' if running else 'false'}\t{json.dumps(labels)}"


class Docker:
    """Answers inspect and stop from a script, and records what it was asked."""

    def __init__(self, *answers, stop_raises=False):
        self._answers = list(answers)
        self.commands: list[list[str]] = []
        self._stop_raises = stop_raises

    async def run(self, args, *, timeout, max_output_bytes):
        self.commands.append(list(args))
        if args[:2] == ["docker", "stop"]:
            if self._stop_raises:
                raise RuntimeError("docker is gone")
            return CommandResult(0, "", "")
        answer = self._answers.pop(0) if self._answers else None
        if isinstance(answer, Exception):
            raise answer
        if answer is None:
            return CommandResult(1, "", "No such object")
        return CommandResult(0, answer, "")

    @property
    def stops(self) -> list[list[str]]:
        return [c for c in self.commands if c[:2] == ["docker", "stop"]]

    @property
    def inspects(self) -> list[list[str]]:
        return [c for c in self.commands if c[:2] == ["docker", "inspect"]]


def stop(docker, handle=None) -> StopOutcome:
    return asyncio.run(stop_container(docker, handle or a_handle()))


class TestReadingWhatDockerSaid(unittest.TestCase):
    def test_a_running_container_with_our_labels(self):
        state = read_state(inspected(running=True))

        self.assertTrue(state.exists)
        self.assertTrue(state.running)
        self.assertTrue(state.belongs_to(a_handle()))

    def test_a_stopped_one(self):
        state = read_state(inspected(running=False))

        self.assertTrue(state.exists)
        self.assertFalse(state.running)

    def test_nothing_at_all(self):
        self.assertFalse(read_state("").exists)

    def test_labels_that_are_not_json(self):
        state = read_state("true\tnot json at all")

        self.assertTrue(state.exists)
        self.assertFalse(state.belongs_to(a_handle()))

    def test_a_container_with_no_labels_is_not_ours(self):
        # Every container this Worker starts is labelled. An unlabelled one
        # under our name came from somewhere else.
        state = read_state("true\tnull")

        self.assertFalse(state.belongs_to(a_handle()))

    def test_another_generation_is_not_ours(self):
        state = read_state(inspected(generation=7))

        self.assertFalse(state.belongs_to(a_handle(generation=1)))

    def test_another_job_is_not_ours(self):
        state = read_state(inspected(job_id="999"))

        self.assertFalse(state.belongs_to(a_handle(job_id="123")))


class TestTheInspectFormatAndTheParserAgree(unittest.TestCase):
    """The gap the first real GPU cancellation fell through.

    Every test above builds the inspect output with a tab of its own, so the
    parser was exercised and the format string never was. Docker emits an
    unrecognised Go-template escape literally, so a backslash-t in the format
    produced the two characters, the parser found no separator, and a
    container this process had just started came back as NOT_OURS -- the
    training ran to completion with a cancellation outstanding.
    """

    def test_the_separator_is_a_real_tab(self):
        self.assertEqual(FIELD_SEPARATOR, "	")
        self.assertEqual(len(FIELD_SEPARATOR), 1)

    def test_the_format_carries_that_same_separator(self):
        fmt = inspect_command("x")[3]

        self.assertIn(FIELD_SEPARATOR, fmt)
        # The two characters, not the tab -- Docker would echo them.
        self.assertNotIn(chr(92) + "t", fmt,
                         "the escape was left for Docker to read")

    def test_output_shaped_by_the_format_parses(self):
        # Assembled from the format's own separator rather than a literal, so
        # changing one without the other fails here.
        fmt = inspect_command("x")[3]
        before, _, after = fmt.partition(FIELD_SEPARATOR)
        self.assertEqual(before, "{{.State.Running}}")
        self.assertEqual(after, "{{json .Config.Labels}}")

        state = read_state("true" + FIELD_SEPARATOR + json.dumps(
            {LABEL_JOB_ID: JOB, LABEL_GENERATION: "1"}))

        self.assertTrue(state.running)
        self.assertTrue(state.belongs_to(a_handle()))

    def test_a_literal_escape_in_the_output_is_not_read_as_ours(self):
        # What Docker actually produced before the fix.
        state = read_state("true" + chr(92) + 't{"gshared.job-id":"123"}')

        self.assertFalse(state.belongs_to(a_handle()))


class TestTheStopCommand(unittest.TestCase):
    def test_it_is_a_bounded_docker_stop(self):
        self.assertEqual(stop_command("gshared-job-123"),
                         ["docker", "stop", "--time",
                          str(STOP_GRACE_SECONDS), "gshared-job-123"])

    def test_the_grace_period_is_explicit(self):
        # Not Docker's ten-second default: a number this repository chose.
        self.assertIn("--time", stop_command("x"))

    def test_it_is_not_a_kill(self):
        # SIGTERM first. A training process given a moment leaves its files
        # closed and the CUDA context released.
        self.assertNotIn("kill", stop_command("x"))

    def test_it_is_a_list_not_a_shell_string(self):
        for part in stop_command("gshared-job-123"):
            self.assertIsInstance(part, str)
            self.assertNotIn(";", part)
            self.assertNotIn("&", part)


class TestOnlyOurOwnContainer(unittest.TestCase):
    def test_a_container_with_our_labels_is_stopped(self):
        docker = Docker(inspected(running=True), inspected(running=False))

        self.assertIs(stop(docker), StopOutcome.STOPPED)
        self.assertEqual(len(docker.stops), 1)

    def test_a_container_from_another_generation_is_left_alone(self):
        """The failure this exists for.

        Same name, same job, different run -- so a container this process did
        not start. Stopping it is stopping somebody else's work.
        """
        docker = Docker(inspected(running=True, generation=7))

        self.assertIs(stop(docker, a_handle(generation=1)),
                      StopOutcome.NOT_OURS)
        self.assertEqual(docker.stops, [])

    def test_a_container_for_another_job_is_left_alone(self):
        docker = Docker(inspected(running=True, job_id="999"))

        self.assertIs(stop(docker, a_handle(job_id="123")),
                      StopOutcome.NOT_OURS)
        self.assertEqual(docker.stops, [])

    def test_an_unlabelled_container_is_left_alone(self):
        docker = Docker("true\t{}")

        self.assertIs(stop(docker), StopOutcome.NOT_OURS)
        self.assertEqual(docker.stops, [])

    def test_it_looks_before_it_stops(self):
        docker = Docker(inspected(running=True), inspected(running=False))

        stop(docker)

        self.assertEqual(docker.commands[0][:2], ["docker", "inspect"])


class TestWhatIsTrueAfterwards(unittest.TestCase):
    def test_a_container_that_is_gone_is_stopped(self):
        # With ``--rm`` this is what a stopped container looks like.
        docker = Docker(inspected(running=True), None)

        self.assertIs(stop(docker), StopOutcome.STOPPED)

    def test_a_container_that_was_never_there_needs_no_stop(self):
        docker = Docker(None)

        self.assertIs(stop(docker), StopOutcome.ALREADY_GONE)
        self.assertEqual(docker.stops, [])

    def test_a_container_that_had_already_exited_needs_no_stop(self):
        docker = Docker(inspected(running=False))

        self.assertIs(stop(docker), StopOutcome.ALREADY_GONE)
        self.assertEqual(docker.stops, [])

    def test_a_container_still_running_is_reported_as_such(self):
        """The answer that must never be followed by an abort.

        The deposit would go back to the customer while this machine went on
        training at its own expense.
        """
        docker = Docker(inspected(running=True), inspected(running=True))

        self.assertIs(stop(docker), StopOutcome.STILL_RUNNING)

    def test_still_running_is_not_a_kind_of_stopped(self):
        self.assertFalse(StopOutcome.STILL_RUNNING.is_stopped)
        self.assertFalse(StopOutcome.UNKNOWN.is_stopped)
        self.assertFalse(StopOutcome.NOT_OURS.is_stopped)
        self.assertTrue(StopOutcome.STOPPED.is_stopped)
        self.assertTrue(StopOutcome.ALREADY_GONE.is_stopped)

    def test_a_stop_command_that_failed_is_decided_by_looking(self):
        # The exit code says whether the command ran. The second inspect says
        # whether the container is gone, which is the question.
        docker = Docker(inspected(running=True), inspected(running=False),
                        stop_raises=True)

        self.assertIs(stop(docker), StopOutcome.STOPPED)

    def test_a_stop_that_failed_and_left_it_running_is_still_running(self):
        docker = Docker(inspected(running=True), inspected(running=True),
                        stop_raises=True)

        self.assertIs(stop(docker), StopOutcome.STILL_RUNNING)

    def test_docker_that_cannot_be_asked_at_all_is_unknown(self):
        docker = Docker(RuntimeError("no docker"))

        self.assertIs(stop(docker), StopOutcome.UNKNOWN)

    def test_docker_that_stops_answering_after_the_stop_is_unknown(self):
        docker = Docker(inspected(running=True), RuntimeError("no docker"))

        self.assertIs(stop(docker), StopOutcome.UNKNOWN)

    def test_it_looks_again_after_stopping(self):
        # A stop that returned zero is Docker saying it tried.
        docker = Docker(inspected(running=True), inspected(running=False))

        stop(docker)

        self.assertEqual(len(docker.inspects), 2)


class TestTheHandleRecordsWhatHappened(unittest.TestCase):
    def test_a_successful_stop_is_recorded(self):
        handle = a_handle()
        stop(Docker(inspected(running=True), inspected(running=False)), handle)

        self.assertTrue(handle.stop_requested)
        self.assertIs(handle.outcome, StopOutcome.STOPPED)

    def test_a_container_left_alone_was_never_asked(self):
        handle = a_handle(generation=1)
        stop(Docker(inspected(running=True, generation=9)), handle)

        self.assertFalse(handle.stop_requested)
        self.assertIs(handle.outcome, StopOutcome.NOT_OURS)

    def test_the_name_comes_from_the_job(self):
        self.assertEqual(a_handle("456").container_name, "gshared-job-456")


if __name__ == "__main__":
    unittest.main()
