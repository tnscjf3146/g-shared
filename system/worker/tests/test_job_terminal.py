"""The last message a Job sends, and why it cannot be lost.

Progress and terminal answers cross the same thread boundary and are treated
in opposite ways, which is the point of this file.

Progress is a **state**: the newest reading replaces the last, and a run that
produced ten thousand steps publishes one number. Dropping one costs a
percentage that is right again in two seconds.

A terminal answer is an **event**: losing it means the Backend holds the Job
RUNNING for ever, the supplier is never paid and the client's deposit has
already moved on chain. So it is kept until a send has actually returned, it
survives a reconnection, and the Job's slot is not given back until it has
gone out.
"""

from __future__ import annotations

import ast
import inspect
import json
import threading
import unittest

from gshared_worker.job_acceptance import JobAcceptance, Work
from gshared_worker.job_runtime import RuntimeFailure, RuntimeOutcome, RuntimeState
from gshared_worker.job_settlement import JobFailureReady, JobResultReady, Unsettled
from gshared_worker.runtime_progress import ProgressChannel, TerminalChannel
from gshared_worker.worker_connection import (
    HEARTBEAT_ACK_TIMEOUT_SECONDS,
    HEARTBEAT_INTERVAL_SECONDS,
    _every_turn,
    keep_alive,
    send_terminal,
)
from gshared_worker.worker_job import JobSlot, read_assignment
from gshared_worker.worker_socket import ConnectionWentAway, RegisteredSocket

from tests.test_worker_connection import Clock, FakeBackend, Stop

OPERATOR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"

FINISHED = RuntimeOutcome(RuntimeState.FINISHED, None, "training finished", 0,
                          actual_runtime_sec=982.4)
FAILED = RuntimeOutcome(RuntimeState.FAILED, RuntimeFailure.CUDA_OOM,
                        "the GPU ran out of memory", 13,
                        actual_runtime_sec=41.2)


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


def a_result(job_id="123", tx="0x" + "cd" * 32, seconds=982.4):
    return JobResultReady(job_id, f"jobs/{job_id}/result.tar",
                          "0x" + "ab" * 32, tx, seconds)


def a_failure(job_id="123", code="CUDA_OOM", tx="0x" + "ef" * 32, seconds=41.2):
    return JobFailureReady(job_id, code, "the GPU ran out of memory", tx,
                           seconds)


def holding(job_id="123") -> JobSlot:
    slot = JobSlot()
    slot.offer(a_job(job_id))
    slot.confirm()
    return slot


class Socket:
    def __init__(self, fail_times=0):
        self.sent: list[dict] = []
        self._fail_times = fail_times

    def send(self, message):
        if self._fail_times > 0:
            self._fail_times -= 1
            raise ConnectionWentAway("the socket closed")
        self.sent.append(message)

    def of(self, kind):
        return [m for m in self.sent if m["type"] == kind]


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


class TestTheChannelKeepsWhatProgressDrops(unittest.TestCase):
    def test_taking_does_not_consume(self):
        channel = TerminalChannel()
        channel.put(a_result())

        self.assertIsNotNone(channel.take())
        self.assertIsNotNone(channel.take(), "it was consumed by reading")

    def test_only_an_explicit_done_clears_it(self):
        channel = TerminalChannel()
        terminal = a_result()
        channel.put(terminal)

        self.assertTrue(channel.done(terminal))
        self.assertIsNone(channel.take())

    def test_a_stale_done_does_not_clear_a_newer_answer(self):
        channel = TerminalChannel()
        first = a_result()
        channel.put(first)
        channel.done(first)
        second = a_failure()
        channel.put(second)

        self.assertFalse(channel.done(first))
        self.assertIsNotNone(channel.take())

    def test_a_second_answer_for_the_same_job_is_ignored(self):
        # A duplicated settlement would otherwise overwrite the answer that
        # was reasoned about with one that was not.
        channel = TerminalChannel()
        channel.put(a_result())

        self.assertFalse(channel.put(a_failure()))
        self.assertIsInstance(channel.take(), JobResultReady)

    def test_another_jobs_answer_does_not_evict_an_unsent_one(self):
        channel = TerminalChannel()
        channel.put(a_result("123"))

        self.assertFalse(channel.put(a_result("456")))
        self.assertEqual(channel.take().job_id, "123")

    def test_ten_thousand_progress_updates_coalesce_and_the_answer_does_not(self):
        """The two channels, side by side, doing opposite things."""
        from gshared_worker.runtime_progress import RuntimeProgressEvent

        progress = ProgressChannel()
        terminal = TerminalChannel()
        terminal.put(a_result())
        generation = progress.next_generation()

        for step in range(1, 10001):
            progress.publish(RuntimeProgressEvent(
                "123", generation, step / 100.0, step, 10000, step // 10))

        taken = progress.take()
        self.assertEqual(taken.current_step, 10000)
        self.assertIsNone(progress.take())
        self.assertIsNotNone(terminal.take(), "the answer was coalesced away")


class TestSendingTheAnswer(unittest.TestCase):
    def ready(self, terminal, job_id="123"):
        acceptance = JobAcceptance(operator_address=OPERATOR)
        self.addCleanup(acceptance.close)
        acceptance.terminal.put(terminal)
        return acceptance, Socket(), holding(job_id)

    def test_a_result_goes_out_as_job_result(self):
        acceptance, socket, slot = self.ready(a_result())

        self.assertTrue(send_terminal(acceptance, socket, slot,
                                      clock=lambda: "t"))
        self.assertEqual(len(socket.of("JOB_RESULT")), 1)

    def test_a_failure_goes_out_as_job_failed(self):
        acceptance, socket, slot = self.ready(a_failure())

        self.assertTrue(send_terminal(acceptance, socket, slot,
                                      clock=lambda: "t"))
        self.assertEqual(len(socket.of("JOB_FAILED")), 1)

    def test_the_payload_is_what_the_handler_reads(self):
        acceptance, socket, slot = self.ready(a_result())

        send_terminal(acceptance, socket, slot, clock=lambda: "t")

        payload = socket.of("JOB_RESULT")[0]["payload"]
        self.assertEqual(payload["objectKey"], "jobs/123/result.tar")
        self.assertEqual(payload["resultHash"], "0x" + "ab" * 32)
        self.assertEqual(payload["submitTxHash"], "0x" + "cd" * 32)
        self.assertEqual(payload["actualRuntimeSec"], 982.4)

    def test_the_runtime_is_a_real_number_not_a_whole_one(self):
        # doubleOrNull into a DOUBLE column -- V7 widened it from INT.
        acceptance, socket, slot = self.ready(a_result(seconds=982.4))

        send_terminal(acceptance, socket, slot, clock=lambda: "t")

        value = socket.of("JOB_RESULT")[0]["payload"]["actualRuntimeSec"]
        self.assertIsInstance(value, float)
        self.assertEqual(value, 982.4)

    def test_an_absent_transaction_is_left_out_not_invented(self):
        # No tx is sent when the chain already held this result. CHAR(66)
        # would happily store a made-up one, for ever.
        acceptance, socket, slot = self.ready(a_result(tx=None))

        send_terminal(acceptance, socket, slot, clock=lambda: "t")

        self.assertNotIn("submitTxHash",
                         socket.of("JOB_RESULT")[0]["payload"])

    def test_it_is_json_a_jackson_reader_can_take(self):
        acceptance, socket, slot = self.ready(a_result())

        send_terminal(acceptance, socket, slot, clock=lambda: "t")

        text = json.dumps(socket.sent[0])
        self.assertEqual(json.loads(text), socket.sent[0])

    def test_the_answer_is_let_go_once_it_has_gone(self):
        acceptance, socket, slot = self.ready(a_result())

        send_terminal(acceptance, socket, slot, clock=lambda: "t")

        self.assertTrue(acceptance.terminal.is_empty)
        self.assertFalse(send_terminal(acceptance, socket, slot))

    def test_with_nothing_pending_nothing_is_sent(self):
        acceptance = JobAcceptance(operator_address=OPERATOR)
        self.addCleanup(acceptance.close)

        self.assertFalse(send_terminal(acceptance, Socket(), holding()))

    def test_an_answer_for_another_job_is_not_sent(self):
        acceptance, socket, slot = self.ready(a_result("456"), job_id="123")

        self.assertFalse(send_terminal(acceptance, socket, slot))
        self.assertEqual(socket.sent, [])


class TestTheSlotIsHeldUntilItIsTold(unittest.TestCase):
    def setUp(self):
        self.acceptance = JobAcceptance(operator_address=OPERATOR)
        self.addCleanup(self.acceptance.close)

    def test_a_settled_job_still_holds_its_slot(self):
        self.acceptance.terminal.put(a_result())
        slot = holding()

        self.assertFalse(slot.is_idle)

    def test_the_slot_is_released_after_a_successful_send(self):
        self.acceptance.terminal.put(a_result())
        slot = holding()

        send_terminal(self.acceptance, Socket(), slot, clock=lambda: "t")

        self.assertTrue(slot.is_idle)

    def test_a_failure_releases_it_too(self):
        self.acceptance.terminal.put(a_failure())
        slot = holding()

        send_terminal(self.acceptance, Socket(), slot, clock=lambda: "t")

        self.assertTrue(slot.is_idle)

    def test_a_send_that_failed_keeps_both_the_slot_and_the_answer(self):
        self.acceptance.terminal.put(a_result())
        slot = holding()

        self.assertFalse(send_terminal(self.acceptance, Socket(fail_times=1),
                                       slot, clock=lambda: "t"))
        self.assertFalse(slot.is_idle, "the Job was given up unreported")
        self.assertFalse(self.acceptance.terminal.is_empty)

    def test_the_next_connection_sends_what_the_last_one_could_not(self):
        self.acceptance.terminal.put(a_result())
        slot = holding()

        send_terminal(self.acceptance, Socket(fail_times=1), slot,
                      clock=lambda: "t")
        working = Socket()
        self.assertTrue(send_terminal(self.acceptance, working, slot,
                                      clock=lambda: "t"))

        self.assertEqual(len(working.of("JOB_RESULT")), 1)
        self.assertTrue(slot.is_idle)

    def test_it_is_sent_exactly_once_however_many_turns_run(self):
        self.acceptance.terminal.put(a_result())
        slot = holding()
        socket = Socket()

        for _ in range(20):
            send_terminal(self.acceptance, socket, slot, clock=lambda: "t")

        self.assertEqual(len(socket.of("JOB_RESULT")), 1)


class TestComingBackAfterTheLineDropped(unittest.TestCase):
    """A terminal answer survives a reconnection. Progress does not have to."""

    def test_the_answer_waits_through_a_disconnection(self):
        acceptance = JobAcceptance(operator_address=OPERATOR)
        self.addCleanup(acceptance.close)
        acceptance.terminal.put(a_result())
        slot = holding()

        # Three connections that all failed.
        for _ in range(3):
            send_terminal(acceptance, Socket(fail_times=1), slot,
                          clock=lambda: "t")

        working = Socket()
        send_terminal(acceptance, working, slot, clock=lambda: "t")

        self.assertEqual(len(working.of("JOB_RESULT")), 1)

    def test_a_failure_answer_waits_too(self):
        acceptance = JobAcceptance(operator_address=OPERATOR)
        self.addCleanup(acceptance.close)
        acceptance.terminal.put(a_failure())
        slot = holding()

        send_terminal(acceptance, Socket(fail_times=1), slot, clock=lambda: "t")
        working = Socket()
        send_terminal(acceptance, working, slot, clock=lambda: "t")

        self.assertEqual(len(working.of("JOB_FAILED")), 1)

    def test_no_container_is_started_again_by_any_of_it(self):
        started: list = []
        acceptance = JobAcceptance(
            run_job=lambda job, progress=None: started.append(1) or FINISHED,
            operator_address=OPERATOR)
        self.addCleanup(acceptance.close)
        acceptance.terminal.put(a_result())
        slot = holding()

        for _ in range(5):
            send_terminal(acceptance, Socket(fail_times=1), slot,
                          clock=lambda: "t")
        send_terminal(acceptance, Socket(), slot, clock=lambda: "t")

        self.assertEqual(started, [])

    def test_the_settlement_is_not_run_again_while_an_answer_waits(self):
        settled: list = []
        acceptance = JobAcceptance(
            settle_job=lambda job, outcome: settled.append(1) or a_result(),
            operator_address=OPERATOR)
        self.addCleanup(acceptance.close)

        self.assertTrue(acceptance.start_settlement(a_job(), FINISHED))
        acceptance.close()

        self.assertFalse(acceptance.start_settlement(a_job(), FINISHED))
        self.assertEqual(len(settled), 1)


class TestTheHeartbeatIsNotBlocked(unittest.TestCase):
    def test_settling_happens_off_the_connection_loop(self):
        """An upload and a transaction can take minutes.

        The loop turns while the settlement runs, so the beats keep going --
        which is what stops the Backend's sweeper reassigning a Job whose
        result is halfway to storage.
        """
        release = threading.Event()
        started = threading.Event()
        self.addCleanup(release.set)

        def slow(job, outcome):
            started.set()
            release.wait(timeout=10)
            return a_result()

        stop = Stop()
        clock = Clock()
        backend = FakeBackend(clock=clock, stop=stop, beats=4)
        socket = RegisteredSocket(backend, "worker-001")
        slot = holding()
        acceptance = JobAcceptance(settle_job=slow, operator_address=OPERATOR)
        self.addCleanup(acceptance.close)
        acceptance.start_settlement(a_job(), FINISHED)
        started.wait(timeout=5)

        def each_turn():
            if len(backend.heartbeats) >= 3:
                release.set()
            _every_turn(acceptance, socket, slot, lambda *p: None)

        keep_alive(socket, stop, interval=HEARTBEAT_INTERVAL_SECONDS,
                   ack_timeout=HEARTBEAT_ACK_TIMEOUT_SECONDS, monotonic=clock,
                   on_message=lambda message: None, each_turn=each_turn)

        self.assertGreaterEqual(len(backend.heartbeats), 3)

    def test_the_answer_goes_out_on_a_later_turn_of_the_same_loop(self):
        stop = Stop()
        clock = Clock()
        backend = FakeBackend(clock=clock, stop=stop, beats=2)
        socket = RegisteredSocket(backend, "worker-001")
        slot = holding()
        acceptance = JobAcceptance(operator_address=OPERATOR)
        self.addCleanup(acceptance.close)
        acceptance.terminal.put(a_result())

        keep_alive(socket, stop, interval=HEARTBEAT_INTERVAL_SECONDS,
                   ack_timeout=HEARTBEAT_ACK_TIMEOUT_SECONDS, monotonic=clock,
                   on_message=lambda message: None,
                   each_turn=lambda: _every_turn(
                       acceptance, socket, slot, lambda *p: None))

        self.assertEqual(len(backend._of_type("JOB_RESULT")), 1)
        self.assertTrue(slot.is_idle)


class TestNothingElseSends(unittest.TestCase):
    def test_only_the_connection_module_builds_a_terminal_message(self):
        from gshared_worker import (
            job_acceptance,
            job_runtime,
            job_settlement,
            result_artifact,
            result_upload,
            runtime_pipeline,
            runtime_progress,
        )

        for module in (job_settlement, result_upload, result_artifact,
                       runtime_pipeline, runtime_progress, job_runtime,
                       job_acceptance):
            source = code_only(module)
            for absent in ("job_result_message", "job_failed_message",
                           "RegisteredSocket", "socket.send"):
                with self.subTest(module=module.__name__, absent=absent):
                    self.assertNotIn(absent, source)

    def test_the_settlement_does_not_touch_the_slot(self):
        # Releasing a Job from a background thread would let a second Job be
        # taken while the first one's answer was still unsent.
        from gshared_worker import job_settlement

        source = code_only(job_settlement)
        for absent in ("JobSlot", "slot.", "release()"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)


class TestAJobThatCannotBeSettled(unittest.TestCase):
    def test_an_unsettled_job_produces_no_terminal_message(self):
        acceptance = JobAcceptance(
            settle_job=lambda job, outcome: Unsettled(
                "123", "RECONCILIATION_REQUIRED", "the chain holds another result"),
            operator_address=OPERATOR)
        self.addCleanup(acceptance.close)

        acceptance.start_settlement(a_job(), FINISHED)
        acceptance.close()

        self.assertTrue(acceptance.terminal.is_empty)
        self.assertFalse(send_terminal(acceptance, Socket(), holding()))

    def test_it_keeps_its_slot(self):
        acceptance = JobAcceptance(
            settle_job=lambda job, outcome: Unsettled("123", "R", "d"),
            operator_address=OPERATOR)
        self.addCleanup(acceptance.close)
        slot = holding()

        acceptance.start_settlement(a_job(), FINISHED)
        acceptance.close()
        send_terminal(acceptance, Socket(), slot)

        self.assertFalse(slot.is_idle)

    def test_the_supplier_is_told(self):
        from gshared_worker.job_acceptance import Decision
        from gshared_worker.worker_connection import _settle_settlement

        lines: list = []
        _settle_settlement(
            Decision(a_job(), None, Work.SETTLE,
                     Unsettled("123", "RECONCILIATION_REQUIRED",
                               "the chain holds another result")),
            holding(), lambda *p: lines.append(" ".join(str(x) for x in p)))

        self.assertTrue(any("정산하지 못했습니다" in line for line in lines))

    def test_a_settlement_that_raised_leaves_no_answer(self):
        def explode(job, outcome):
            raise RuntimeError("boom")

        acceptance = JobAcceptance(settle_job=explode,
                                   operator_address=OPERATOR)
        self.addCleanup(acceptance.close)

        acceptance.start_settlement(a_job(), FINISHED)
        acceptance.close()

        self.assertTrue(acceptance.terminal.is_empty)
        decision = acceptance.take_decisions()[0]
        self.assertIs(decision.work, Work.SETTLE)
        self.assertFalse(decision.result.accepted)


class TestTheRuntimeHandsOverToTheSettlement(unittest.TestCase):
    def test_a_finished_container_starts_a_settlement(self):
        seen: list = []
        acceptance = JobAcceptance(
            run_job=lambda job, progress=None: FINISHED,
            settle_job=lambda job, outcome: seen.append(outcome) or a_result(),
            operator_address=OPERATOR)
        self.addCleanup(acceptance.close)

        acceptance.start_runtime(a_job())
        acceptance.close()
        acceptance._thread = None
        acceptance.start_settlement(a_job(), FINISHED)
        acceptance.close()

        self.assertEqual(len(seen), 1)
        self.assertIs(seen[0].state, RuntimeState.FINISHED)

    def test_a_failed_container_does_too(self):
        seen: list = []
        acceptance = JobAcceptance(
            settle_job=lambda job, outcome: seen.append(outcome) or a_failure(),
            operator_address=OPERATOR)
        self.addCleanup(acceptance.close)

        acceptance.start_settlement(a_job(), FAILED)
        acceptance.close()

        self.assertIs(seen[0].state, RuntimeState.FAILED)
        self.assertIsInstance(acceptance.terminal.take(), JobFailureReady)

    def test_without_a_settlement_configured_nothing_starts(self):
        acceptance = JobAcceptance(operator_address=OPERATOR)
        self.addCleanup(acceptance.close)

        self.assertFalse(acceptance.start_settlement(a_job(), FINISHED))


if __name__ == "__main__":
    unittest.main()
