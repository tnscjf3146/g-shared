"""JOB_PROGRESS: the message, and who is allowed to send it.

Two things are being protected here.

The **contract** -- read from ``WorkerWebSocketHandler.handleJobProgress`` and
``JobRepository.updateProgressIfRunning``, not from the circulating spec. The
two disagree about ``elapsedSec``: the spec shows ``123.4``, the handler reads
it with ``intOrNull`` into an ``INT`` column. The code is what stores the row.

The **ownership** -- a training run lives on a background thread and the
WebSocket has exactly one reader and one writer. Progress crosses that
boundary as a value, and the connection loop is what puts it on the wire. The
tests below check that the runtime cannot reach a socket even if somebody
later wishes it could.
"""

from __future__ import annotations

import ast
import inspect
import json
import threading
import unittest

from gshared_worker.job_acceptance import JobAcceptance
from gshared_worker.job_runtime import (
    RuntimeOutcome,
    RuntimeState,
)
from gshared_worker.runtime_progress import ProgressChannel, RuntimeProgressEvent
from gshared_worker.worker_connection import (
    HEARTBEAT_ACK_TIMEOUT_SECONDS,
    HEARTBEAT_INTERVAL_SECONDS,
    _every_turn,
    keep_alive,
    send_progress,
)
from gshared_worker.worker_job import JobSlot, job_progress_message, read_assignment
from gshared_worker.worker_socket import RegisteredSocket

from tests.test_worker_connection import Clock, FakeBackend, Stop

OPERATOR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"

FINISHED = RuntimeOutcome(RuntimeState.FINISHED, None, "training finished", 0)


def assignment(job_id="123"):
    return {
        "type": "JOB_ASSIGN", "jobId": job_id,
        "timestamp": "2026-09-11T01:00:00Z",
        "payload": {
            "dockerImageId": 1, "modelId": 3, "datasetId": 42,
            "trainingMode": "AUTO",
            "trainingConfig": {"epochs": 3, "batchSize": 8,
                               "learningRate": 0.0002, "maxLength": 1024},
            "escrowJobId": "17", "externalRef": "0x" + "ab" * 32,
            "price": "9000000000000000000",
            "clientWallet": "0x1111111111111111111111111111111111111111",
            "providerWallet": "0x2222222222222222222222222222222222222222",
        },
    }


def a_job(job_id="123"):
    return read_assignment(assignment(job_id))


def a_progress(job_id="123", generation=1, step=85, percent=42.5,
               epoch=2, total_epochs=3, elapsed=123):
    return RuntimeProgressEvent(
        job_id=job_id, generation=generation, progress_percent=percent,
        current_step=step, total_steps=200, elapsed_sec=elapsed,
        current_epoch=epoch, total_epochs=total_epochs)


def holding(job_id="123", confirmed=True) -> JobSlot:
    slot = JobSlot()
    slot.offer(a_job(job_id))
    if confirmed:
        slot.confirm()
    return slot


class Socket:
    """Just enough of RegisteredSocket to record what went out."""

    def __init__(self, fail=None):
        self.sent: list[dict] = []
        self._fail = fail

    def send(self, message):
        if self._fail is not None:
            raise self._fail
        self.sent.append(message)

    @property
    def progress(self) -> list[dict]:
        return [m for m in self.sent if m["type"] == "JOB_PROGRESS"]


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


class TestTheMessageMatchesTheBackend(unittest.TestCase):
    def setUp(self):
        self.message = job_progress_message(a_progress(), "2026-09-11T05:00:00Z")
        self.payload = self.message["payload"]

    def test_the_envelope(self):
        self.assertEqual(self.message["type"], "JOB_PROGRESS")
        self.assertEqual(self.message["jobId"], "123")
        self.assertEqual(self.message["timestamp"], "2026-09-11T05:00:00Z")

    def test_the_job_id_is_in_the_envelope_not_the_payload(self):
        # parseJobId reads msg.jobId(). A payload jobId would be ignored.
        self.assertNotIn("jobId", self.payload)

    def test_the_job_id_is_a_string_the_backend_can_parse_as_a_long(self):
        self.assertIsInstance(self.message["jobId"], str)
        self.assertEqual(int(self.message["jobId"]), 123)

    def test_every_field_the_handler_reads(self):
        for field in ("progressPercent", "currentStep", "totalSteps",
                      "currentEpoch", "totalEpochs", "elapsedSec"):
            with self.subTest(field=field):
                self.assertIn(field, self.payload)

    def test_the_percentage_is_a_real_number(self):
        # doubleOrNull, into a DOUBLE column. 43.75 must survive as 43.75.
        self.assertIsInstance(self.payload["progressPercent"], float)
        self.assertEqual(self.payload["progressPercent"], 42.5)

    def test_everything_else_is_a_whole_number(self):
        """Including ``elapsedSec``, which the spec shows as a float.

        ``intOrNull`` calls ``asInt()``, which truncates silently, and the
        column is ``INT``. A float here would be a mismatch nobody notices
        until a number looks slightly wrong months later.
        """
        for field in ("currentStep", "totalSteps", "currentEpoch",
                      "totalEpochs", "elapsedSec"):
            with self.subTest(field=field):
                value = self.payload[field]
                self.assertIsInstance(value, int)
                self.assertNotIsInstance(value, bool)

    def test_an_unknown_epoch_is_left_out_rather_than_sent_as_null(self):
        without = job_progress_message(
            a_progress(epoch=None, total_epochs=None), "t")

        self.assertNotIn("currentEpoch", without["payload"])
        self.assertNotIn("totalEpochs", without["payload"])
        self.assertIn("progressPercent", without["payload"])

    def test_it_is_json_a_jackson_reader_can_take(self):
        text = json.dumps(self.message)

        self.assertEqual(json.loads(text), self.message)

    def test_nothing_but_numbers_is_in_it(self):
        """The security rule, as a test.

        No dataset, no signed URL, no local path, no token, no line of the
        container's output. The payload is six numbers.
        """
        for value in self.payload.values():
            self.assertIsInstance(value, (int, float))

    def test_the_payload_carries_no_credential_shaped_key(self):
        for forbidden in ("token", "url", "path", "key", "secret", "dataset",
                          "stdout", "stderr", "loss", "message", "operator"):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(
                    any(forbidden in key.lower() for key in self.payload))


class TestOnlyTheConnectionSends(unittest.TestCase):
    def test_the_runtime_layers_never_touch_a_socket(self):
        from gshared_worker import job_runtime, runtime_pipeline, runtime_progress

        for module in (runtime_progress, runtime_pipeline, job_runtime):
            source = code_only(module)
            for absent in ("RegisteredSocket", "socket", "ws.send", "websocket",
                           "heartbeat_message", "job_progress_message"):
                with self.subTest(module=module.__name__, absent=absent):
                    self.assertNotIn(absent, source)

    def test_the_runtime_layers_do_not_build_the_message(self):
        from gshared_worker import runtime_pipeline, runtime_progress

        for module in (runtime_progress, runtime_pipeline):
            with self.subTest(module=module.__name__):
                self.assertNotIn("JOB_PROGRESS", code_only(module))

    def test_no_second_thread_reads_the_connection(self):
        from gshared_worker import worker_connection

        self.assertNotIn("threading.Thread", code_only(worker_connection))

    def test_the_terminal_messages_are_still_not_written(self):
        # Task 9. Nothing here should have reached for them.
        from gshared_worker import (
            job_runtime,
            runtime_pipeline,
            runtime_progress,
            worker_connection,
        )

        for module in (runtime_progress, runtime_pipeline, job_runtime,
                       worker_connection):
            source = code_only(module)
            for absent in ("JOB_RESULT", "JOB_FAILED", "submitResult",
                           "resultHash", "result-upload"):
                with self.subTest(module=module.__name__, absent=absent):
                    self.assertNotIn(absent, source)


class TestItOnlyGoesOutForTheJobInHand(unittest.TestCase):
    def make(self, job_id="123", confirmed=True, progress=None):
        acceptance = JobAcceptance(operator_address=OPERATOR)
        self.addCleanup(acceptance.close)
        acceptance.progress.publish(progress or a_progress())
        return acceptance, Socket(), holding(job_id, confirmed)

    def test_it_is_sent_for_the_job_being_held(self):
        acceptance, socket, slot = self.make()

        self.assertTrue(send_progress(acceptance, socket, slot, lambda: "t"))
        self.assertEqual(len(socket.progress), 1)

    def test_an_update_for_another_job_is_dropped(self):
        """The Backend would apply it to whatever row the id named."""
        acceptance, socket, slot = self.make(job_id="456")

        self.assertFalse(send_progress(acceptance, socket, slot, lambda: "t"))
        self.assertEqual(socket.sent, [])

    def test_nothing_is_sent_once_the_slot_is_empty(self):
        acceptance, socket, slot = self.make()
        slot.release()

        self.assertFalse(send_progress(acceptance, socket, slot, lambda: "t"))

    def test_nothing_is_sent_while_the_job_is_still_being_accepted(self):
        # The Backend moves the Job to RUNNING on JOB_ACCEPTED. Before that it
        # would drop the update anyway -- and this Worker is not training yet.
        acceptance, socket, slot = self.make(confirmed=False)

        self.assertFalse(send_progress(acceptance, socket, slot, lambda: "t"))

    def test_with_nothing_new_nothing_is_sent(self):
        acceptance = JobAcceptance(operator_address=OPERATOR)
        self.addCleanup(acceptance.close)
        socket, slot = Socket(), holding()

        self.assertFalse(send_progress(acceptance, socket, slot, lambda: "t"))
        self.assertEqual(socket.sent, [])

    def test_one_message_per_turn_at_most(self):
        acceptance, socket, slot = self.make()
        for step in range(2, 40):
            acceptance.progress.publish(a_progress(step=step))

        send_progress(acceptance, socket, slot, lambda: "t")

        self.assertEqual(len(socket.progress), 1)
        self.assertEqual(socket.progress[0]["payload"]["currentStep"], 39)


class TestAFailedSendIsNotAFailedJob(unittest.TestCase):
    def test_the_send_failure_does_not_escape(self):
        """A training run is hours of GPU time. A frame is a frame."""
        from gshared_worker.worker_socket import ConnectionWentAway

        acceptance = JobAcceptance(operator_address=OPERATOR)
        self.addCleanup(acceptance.close)
        acceptance.progress.publish(a_progress())
        socket = Socket(fail=ConnectionWentAway("the socket closed"))

        self.assertFalse(send_progress(acceptance, socket, holding(), lambda: "t"))

    def test_what_did_not_go_out_is_offered_again(self):
        from gshared_worker.worker_socket import ConnectionWentAway

        acceptance = JobAcceptance(operator_address=OPERATOR)
        self.addCleanup(acceptance.close)
        acceptance.progress.publish(a_progress())

        send_progress(acceptance, Socket(fail=ConnectionWentAway("gone")),
                      holding(), lambda: "t")
        working = Socket()
        send_progress(acceptance, working, holding(), lambda: "t")

        self.assertEqual(len(working.progress), 1)

    def test_a_runtime_thread_keeps_going_while_sends_fail(self):
        from gshared_worker.worker_socket import ConnectionWentAway

        still_running = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        def slow(job, progress=None):
            still_running.set()
            release.wait(timeout=5)
            return FINISHED

        acceptance = JobAcceptance(run_job=slow, operator_address=OPERATOR)
        self.addCleanup(acceptance.close)
        acceptance.start_runtime(a_job())
        still_running.wait(timeout=5)

        acceptance.progress.publish(a_progress())
        send_progress(acceptance, Socket(fail=ConnectionWentAway("gone")),
                      holding(), lambda: "t")

        self.assertTrue(acceptance.busy, "the training was given up on")
        release.set()


class TestTheHeartbeatComesFirst(unittest.TestCase):
    def test_beats_keep_going_through_a_flood_of_progress(self):
        """A container emitting constantly must not starve the heartbeat.

        It cannot, structurally: the box holds one value, so a turn of the
        loop sends at most one frame however many steps happened. This drives
        ninety seconds of loop with progress published on every turn and
        checks the beats still went out.
        """
        stop = Stop()
        clock = Clock()
        backend = FakeBackend(clock=clock, stop=stop, beats=3)
        socket = RegisteredSocket(backend, "worker-001")
        slot = holding()
        acceptance = JobAcceptance(operator_address=OPERATOR)
        self.addCleanup(acceptance.close)
        turns = {"n": 0}

        def each_turn():
            turns["n"] += 1
            acceptance.progress.publish(a_progress(step=turns["n"]))
            _every_turn(acceptance, socket, slot, lambda *p: None)

        keep_alive(socket, stop, interval=HEARTBEAT_INTERVAL_SECONDS,
                   ack_timeout=HEARTBEAT_ACK_TIMEOUT_SECONDS, monotonic=clock,
                   on_message=lambda message: None, each_turn=each_turn)

        self.assertGreaterEqual(len(backend.heartbeats), 3)

    def test_the_acks_were_all_handled(self):
        stop = Stop()
        clock = Clock()
        backend = FakeBackend(clock=clock, stop=stop, beats=3)
        socket = RegisteredSocket(backend, "worker-001")
        slot = holding()
        acceptance = JobAcceptance(operator_address=OPERATOR)
        self.addCleanup(acceptance.close)

        outcome, _ = keep_alive(
            socket, stop, interval=HEARTBEAT_INTERVAL_SECONDS,
            ack_timeout=HEARTBEAT_ACK_TIMEOUT_SECONDS, monotonic=clock,
            on_message=lambda message: None,
            each_turn=lambda: (
                acceptance.progress.publish(a_progress()),
                _every_turn(acceptance, socket, slot, lambda *p: None)))

        # A missed ACK would have ended this as LOST long before the beats ran
        # out. STOPPED means every one of them was answered and handled.
        self.assertEqual(outcome.value, "STOPPED")


class TestComingBackAfterTheLineDropped(unittest.TestCase):
    """Training carries on. What the Backend is told afterwards is one number.

    Before this, a Worker whose connection dropped at 32% and came back at
    37% told the Backend nothing until the next step -- which, near the end
    of a long epoch, can be minutes of a Job that looks stalled.
    """

    def test_the_latest_snapshot_goes_out_on_the_new_connection(self):
        channel = ProgressChannel()
        channel.publish(a_progress(step=64, percent=32.0))
        channel.take()                      # the Backend has this one

        for step, percent in ((66, 33.0), (68, 34.0), (74, 37.0)):
            channel.publish(a_progress(step=step, percent=percent))
        channel.take()                      # ... and this, on the old socket

        channel.resend_on_next_take()       # reconnected

        again = channel.take()
        self.assertEqual(again.progress_percent, 37.0)
        self.assertIsNone(channel.take(), "it was offered more than once")

    def test_the_steps_in_between_are_not_replayed(self):
        channel = ProgressChannel()
        for step in range(1, 200):
            channel.publish(a_progress(step=step))

        channel.resend_on_next_take()
        taken = [channel.take()]
        while taken[-1] is not None:
            taken.append(channel.take())

        self.assertEqual(len([one for one in taken if one]), 1)

    def test_registering_again_offers_the_snapshot(self):
        acceptance = JobAcceptance(operator_address=OPERATOR)
        self.addCleanup(acceptance.close)
        acceptance.progress.publish(a_progress())
        acceptance.progress.take()

        # What _connect_until_stopped does after REGISTER_ACK.
        acceptance.progress.resend_on_next_take()

        self.assertIsNotNone(acceptance.progress.take())

    def test_reconnecting_starts_no_second_container(self):
        """The slot and the decider outlive the socket, and so does the run."""
        source = code_only(
            __import__("gshared_worker.worker_connection", fromlist=["x"]))

        # resend offers a value. It must not call anything that starts work.
        self.assertIn("resend_on_next_take", source)
        self.assertNotIn("start_runtime()", source)


class TestTheRunsProgressIsDroppedWhenTheRunEnds(unittest.TestCase):
    def test_a_finished_run_leaves_nothing_for_the_next_job(self):
        acceptance = JobAcceptance(run_job=lambda job, progress=None: FINISHED,
                                   operator_address=OPERATOR)
        self.addCleanup(acceptance.close)

        acceptance.start_runtime(a_job("123"))
        acceptance.close()
        acceptance.progress.publish(a_progress(job_id="123", generation=1))

        # A second Job claims a new generation, which drops the first's.
        acceptance.progress.next_generation()

        self.assertIsNone(acceptance.progress.take())

    def test_the_outcome_still_arrives_whatever_happened_to_the_progress(self):
        """The two channels are separate, and only one of them may drop."""
        acceptance = JobAcceptance(run_job=lambda job, progress=None: FINISHED,
                                   operator_address=OPERATOR)
        self.addCleanup(acceptance.close)

        acceptance.start_runtime(a_job())
        acceptance.close()
        acceptance.progress.next_generation()      # every update discarded

        decisions = acceptance.take_decisions()
        self.assertEqual(len(decisions), 1)
        self.assertTrue(decisions[0].runtime.succeeded)


class TestThePipelineIsToldWhereToPublish(unittest.TestCase):
    def test_a_runtime_that_wants_progress_is_given_a_tracker(self):
        seen = {}

        def run_job(job, progress=None):
            seen["progress"] = progress
            return FINISHED

        acceptance = JobAcceptance(run_job=run_job, operator_address=OPERATOR)
        self.addCleanup(acceptance.close)

        acceptance.start_runtime(a_job())
        acceptance.close()

        self.assertIsNotNone(seen["progress"])
        self.assertEqual(seen["progress"].generation, 1)

    def test_a_runtime_that_does_not_is_called_as_before(self):
        # Every build before this one, and the fakes in the existing tests.
        seen = {}

        def run_job(job):
            seen["job"] = job
            return FINISHED

        acceptance = JobAcceptance(run_job=run_job, operator_address=OPERATOR)
        self.addCleanup(acceptance.close)

        acceptance.start_runtime(a_job())
        acceptance.close()

        self.assertEqual(seen["job"].job_id, "123")

    def test_each_run_gets_its_own_generation(self):
        acceptance = JobAcceptance(run_job=lambda job, progress=None: FINISHED,
                                   operator_address=OPERATOR)
        self.addCleanup(acceptance.close)
        generations = []

        for job_id in ("123", "456"):
            acceptance._run_job = lambda job, progress=None, keep=generations: (
                keep.append(progress.generation) or FINISHED)
            acceptance.start_runtime(a_job(job_id))
            acceptance.close()
            acceptance._thread = None

        self.assertEqual(generations, [1, 2])


class TestTheRealPipelinePassesEventsThrough(unittest.TestCase):
    def test_run_training_reports_events_before_the_container_exits(self):
        import asyncio

        from gshared_worker.command_runner import CommandResult
        from gshared_worker.job_runtime import run_training
        from gshared_worker.job_workspace import JobWorkspace

        seen: list[str] = []

        class Runner:
            """A runner that hands over lines and only then finishes."""

            async def run_streaming(self, command, *, timeout,
                                    max_output_bytes, on_stdout_line=None,
                                    on_stderr_line=None):
                on_stdout_line('GSHARED_EVENT={"event":"training_started",'
                               '"totalSteps":4}')
                on_stdout_line("some noise from pytorch")
                on_stdout_line('GSHARED_EVENT={"event":"step","step":1,'
                               '"totalSteps":4,"elapsedSec":2}')
                seen.append("the container is still running")
                return CommandResult(0, "", "")

        class Training:
            epochs, batch_size, learning_rate, max_length = 1, 4, 0.0002, 512

        published: list = []
        workspace = JobWorkspace.under(
            __import__("pathlib").Path(
                __import__("tempfile").mkdtemp()), "123").prepare()

        asyncio.run(run_training(
            Runner(), "repo@sha256:" + "ab" * 32, workspace,
            workspace.root, Training(), on_event=published.append))

        self.assertEqual(seen, ["the container is still running"])
        self.assertEqual([event.name for event in published],
                         ["training_started", "step"])


if __name__ == "__main__":
    unittest.main()
