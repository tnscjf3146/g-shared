"""Telling the Backend a Job was cancelled -- once, and not before it is true.

JOB_CANCELLED is the third terminal message, and it follows the same rules the
other two already do: it is built in one place, sent by the connection loop
and nobody else, kept until a send actually returns, and it is what releases
the Job's slot.

What is new is that it can *lose*. A result or a failure may have ended the
Job first, in which case there is no JOB_CANCELLED at all -- and the tests
below are mostly about the Worker not saying two different things about one
Job.
"""

from __future__ import annotations

import ast
import inspect
import json
import threading
import unittest

from gshared_worker.job_acceptance import JobAcceptance, Work
from gshared_worker.job_cancellation import (
    ALREADY_ENDED,
    BACKEND_CANCEL_COMPLETION_RACE,
    JobCancelledReady,
    JobTerminal,
)
from gshared_worker.job_runtime import RuntimeFailure, RuntimeOutcome, RuntimeState
from gshared_worker.job_settlement import JobFailureReady, JobResultReady, Unsettled
from gshared_worker.runtime_progress import RuntimeProgressEvent, TerminalChannel
from gshared_worker.worker_connection import (
    HEARTBEAT_ACK_TIMEOUT_SECONDS,
    HEARTBEAT_INTERVAL_SECONDS,
    _every_turn,
    _settle_cancellation,
    cancel_the_job,
    keep_alive,
    send_progress,
    send_terminal,
)
from gshared_worker.worker_job import (
    ACCEPT_VALIDATION_FAILED,
    USER_REQUEST,
    JobSlot,
    job_cancelled_terminal_message,
    read_assignment,
)
from gshared_worker.worker_socket import ConnectionWentAway, RegisteredSocket

from tests.test_worker_connection import Clock, FakeBackend, Stop

OPERATOR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"

FINISHED = RuntimeOutcome(RuntimeState.FINISHED, None, "training finished", 0,
                          actual_runtime_sec=982.4)


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


def a_cancellation(job_id="123", reason=USER_REQUEST):
    return {"type": "JOB_CANCEL", "jobId": job_id, "timestamp": "t",
            "payload": {"reason": reason}}


def cancelled(job_id="123", tx="0x" + "ef" * 32, seconds=42.5):
    return JobCancelledReady(job_id, USER_REQUEST, tx, seconds)


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


class TestTheMessage(unittest.TestCase):
    def setUp(self):
        self.message = job_cancelled_terminal_message(
            cancelled(), "2026-09-11T05:00:00Z")
        self.payload = self.message["payload"]

    def test_the_envelope(self):
        self.assertEqual(self.message["type"], "JOB_CANCELLED")
        self.assertEqual(self.message["jobId"], "123")
        self.assertEqual(self.message["timestamp"], "2026-09-11T05:00:00Z")

    def test_the_reason_is_the_customers(self):
        self.assertEqual(self.payload["reason"], USER_REQUEST)

    def test_it_is_not_the_cleanup_paths_reason(self):
        # Two different cancellations. Reporting one as the other would tell
        # an operator the Backend had refused a Job the customer stopped.
        self.assertNotEqual(self.payload["reason"], ACCEPT_VALIDATION_FAILED)

    def test_the_abort_transaction_is_carried(self):
        self.assertEqual(self.payload["abortTxHash"], "0x" + "ef" * 32)

    def test_the_runtime_is_a_real_number(self):
        # doubleOrNull into a DOUBLE column, like JOB_RESULT's and unlike
        # JOB_PROGRESS's integer elapsedSec.
        self.assertIsInstance(self.payload["actualRuntimeSec"], float)
        self.assertEqual(self.payload["actualRuntimeSec"], 42.5)

    def test_an_absent_transaction_is_left_out_not_invented(self):
        without = job_cancelled_terminal_message(cancelled(tx=None), "t")

        self.assertNotIn("abortTxHash", without["payload"])

    def test_a_job_that_never_ran_reports_no_runtime(self):
        without = job_cancelled_terminal_message(cancelled(seconds=None), "t")

        self.assertNotIn("actualRuntimeSec", without["payload"])
        self.assertIn("reason", without["payload"])

    def test_it_is_json_a_jackson_reader_can_take(self):
        self.assertEqual(json.loads(json.dumps(self.message)), self.message)

    def test_nothing_but_the_three_fields(self):
        self.assertEqual(set(self.payload),
                         {"reason", "abortTxHash", "actualRuntimeSec"})

    def test_no_raw_output_and_no_credential(self):
        text = json.dumps(self.message).lower()
        for forbidden in ("token", "bearer", "sig=", "traceback",
                          "privatekey", "dataset"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)


class TestSendingIt(unittest.TestCase):
    def ready(self, terminal, job_id="123"):
        acceptance = JobAcceptance(operator_address=OPERATOR)
        self.addCleanup(acceptance.close)
        acceptance.terminal.put(terminal)
        return acceptance, Socket(), holding(job_id)

    def test_it_goes_out_as_job_cancelled(self):
        acceptance, socket, slot = self.ready(cancelled())

        self.assertTrue(send_terminal(acceptance, socket, slot,
                                      clock=lambda: "t"))
        self.assertEqual(len(socket.of("JOB_CANCELLED")), 1)

    def test_the_slot_is_released_only_after_it_has_gone(self):
        acceptance, socket, slot = self.ready(cancelled())

        self.assertFalse(slot.is_idle)
        send_terminal(acceptance, socket, slot, clock=lambda: "t")
        self.assertTrue(slot.is_idle)

    def test_a_failed_send_keeps_both_the_slot_and_the_answer(self):
        acceptance = JobAcceptance(operator_address=OPERATOR)
        self.addCleanup(acceptance.close)
        acceptance.terminal.put(cancelled())
        slot = holding()

        self.assertFalse(send_terminal(acceptance, Socket(fail_times=1), slot,
                                       clock=lambda: "t"))
        self.assertFalse(slot.is_idle)
        self.assertFalse(acceptance.terminal.is_empty)

    def test_the_next_connection_sends_it(self):
        acceptance = JobAcceptance(operator_address=OPERATOR)
        self.addCleanup(acceptance.close)
        acceptance.terminal.put(cancelled())
        slot = holding()

        send_terminal(acceptance, Socket(fail_times=1), slot, clock=lambda: "t")
        working = Socket()
        send_terminal(acceptance, working, slot, clock=lambda: "t")

        self.assertEqual(len(working.of("JOB_CANCELLED")), 1)
        self.assertTrue(slot.is_idle)

    def test_it_is_sent_exactly_once_however_many_turns(self):
        acceptance, socket, slot = self.ready(cancelled())

        for _ in range(20):
            send_terminal(acceptance, socket, slot, clock=lambda: "t")

        self.assertEqual(len(socket.of("JOB_CANCELLED")), 1)

    def test_an_answer_for_another_job_is_not_sent(self):
        acceptance, socket, slot = self.ready(cancelled("456"), job_id="123")

        self.assertFalse(send_terminal(acceptance, socket, slot))
        self.assertEqual(socket.sent, [])


class TestOneAnswerPerJob(unittest.TestCase):
    def test_a_cancellation_cannot_evict_a_pending_result(self):
        """The chain already completed. Overwriting it would be a lie."""
        channel = TerminalChannel()
        channel.put(JobResultReady("123", "jobs/123/result.tar",
                                   "0x" + "ab" * 32, "0x" + "cd" * 32, 982.4))

        self.assertFalse(channel.put(cancelled()))
        self.assertIsInstance(channel.take(), JobResultReady)

    def test_a_cancellation_cannot_evict_a_pending_failure(self):
        channel = TerminalChannel()
        channel.put(JobFailureReady("123", "CUDA_OOM", "no memory",
                                    "0x" + "ef" * 32, 41.2))

        self.assertFalse(channel.put(cancelled()))
        self.assertIsInstance(channel.take(), JobFailureReady)

    def test_and_a_result_cannot_evict_a_pending_cancellation(self):
        channel = TerminalChannel()
        channel.put(cancelled())

        self.assertFalse(channel.put(JobResultReady(
            "123", "k", "0x" + "ab" * 32, None, 1.0)))
        self.assertIsInstance(channel.take(), JobCancelledReady)

    def test_only_one_terminal_message_reaches_the_socket(self):
        acceptance = JobAcceptance(operator_address=OPERATOR)
        self.addCleanup(acceptance.close)
        acceptance.terminal.put(cancelled())
        acceptance.terminal.put(JobFailureReady("123", "X", "y", None, 1.0))
        socket, slot = Socket(), holding()

        send_terminal(acceptance, socket, slot, clock=lambda: "t")
        send_terminal(acceptance, socket, slot, clock=lambda: "t")

        self.assertEqual(len(socket.sent), 1)
        self.assertEqual(socket.sent[0]["type"], "JOB_CANCELLED")


class TestNoProgressAfterACancellation(unittest.TestCase):
    def start(self, job_id="123"):
        acceptance = JobAcceptance(
            run_job=lambda job, progress=None, terminal=None: FINISHED,
            cancel_job=lambda job, terminal, outcome: cancelled(job_id),
            operator_address=OPERATOR)
        self.addCleanup(acceptance.close)
        acceptance.start_runtime(a_job(job_id))
        return acceptance

    def test_the_snapshot_is_dropped_when_the_customer_asks(self):
        acceptance = self.start()
        generation = acceptance.progress.next_generation()
        acceptance.progress.publish(RuntimeProgressEvent(
            "123", generation, 42.5, 85, 200, 123))

        acceptance.start_cancellation(a_job(), USER_REQUEST)

        self.assertIsNone(acceptance.progress.peek())

    def test_nothing_new_goes_out_afterwards(self):
        acceptance = self.start()
        acceptance.start_cancellation(a_job(), USER_REQUEST)
        generation = acceptance.progress.next_generation()
        acceptance.progress.publish(RuntimeProgressEvent(
            "123", generation, 50.0, 100, 200, 130))

        sent = send_progress(acceptance, Socket(), holding(), lambda: "t")

        self.assertFalse(sent)

    def test_a_reconnection_does_not_replay_the_old_percentage(self):
        # It would show a customer their cancelled Job still making progress.
        acceptance = self.start()
        generation = acceptance.progress.next_generation()
        acceptance.progress.publish(RuntimeProgressEvent(
            "123", generation, 42.5, 85, 200, 123))
        acceptance.progress.take()

        acceptance.start_cancellation(a_job(), USER_REQUEST)
        acceptance.progress.resend_on_next_take()

        socket = Socket()
        send_progress(acceptance, socket, holding(), lambda: "t")

        self.assertEqual(socket.sent, [])


class TestDuplicateCancellations(unittest.TestCase):
    def test_ten_requests_start_one_workflow(self):
        started: list = []
        acceptance = JobAcceptance(
            run_job=lambda job, progress=None, terminal=None: FINISHED,
            cancel_job=lambda job, terminal, outcome: (
                started.append(1) or cancelled()),
            operator_address=OPERATOR)
        self.addCleanup(acceptance.close)
        acceptance.start_runtime(a_job())

        for _ in range(10):
            self.assertTrue(acceptance.start_cancellation(a_job(), USER_REQUEST))
        for _ in range(30):
            if len(started) >= 1 and acceptance._cancel_thread is not None:
                acceptance._cancel_thread.join(timeout=5)
                break

        self.assertEqual(len(started), 1)

    def test_one_terminal_answer_comes_out(self):
        acceptance = JobAcceptance(
            run_job=lambda job, progress=None, terminal=None: FINISHED,
            cancel_job=lambda job, terminal, outcome: cancelled(),
            operator_address=OPERATOR)
        self.addCleanup(acceptance.close)
        acceptance.start_runtime(a_job())

        for _ in range(5):
            acceptance.start_cancellation(a_job(), USER_REQUEST)
        if acceptance._cancel_thread is not None:
            acceptance._cancel_thread.join(timeout=5)

        socket, slot = Socket(), holding()
        for _ in range(5):
            send_terminal(acceptance, socket, slot, clock=lambda: "t")

        self.assertEqual(len(socket.of("JOB_CANCELLED")), 1)


class TestACancellationForSomethingElse(unittest.TestCase):
    def ask(self, message, slot):
        acceptance = JobAcceptance(
            run_job=lambda job, progress=None, terminal=None: FINISHED,
            cancel_job=lambda job, terminal, outcome: cancelled(),
            operator_address=OPERATOR)
        self.addCleanup(acceptance.close)
        lines: list = []
        cancel_the_job(message, slot,
                       lambda *p: lines.append(" ".join(str(x) for x in p)),
                       acceptance)
        return acceptance, lines

    def test_a_cancellation_for_another_job_changes_nothing(self):
        acceptance, lines = self.ask(a_cancellation("456"), holding("123"))

        self.assertIsNone(acceptance.terminal_for)
        self.assertTrue(any("holding 123" in line for line in lines))

    def test_a_cancellation_with_no_job_held_changes_nothing(self):
        acceptance, lines = self.ask(a_cancellation("123"), JobSlot())

        self.assertIsNone(acceptance.terminal_for)
        self.assertTrue(any("not holding it" in line for line in lines))

    def test_an_unknown_reason_is_not_read_as_a_user_cancellation(self):
        acceptance, _ = self.ask(a_cancellation("123", "SOMETHING_NEW"),
                                 holding("123"))

        self.assertIsNone(acceptance.terminal_for)

    def test_a_missing_reason_is_not_either(self):
        message = {"type": "JOB_CANCEL", "jobId": "123", "timestamp": "t",
                   "payload": {}}
        acceptance, _ = self.ask(message, holding("123"))

        self.assertIsNone(acceptance.terminal_for)


class TestTheHeartbeatKeepsGoing(unittest.TestCase):
    def test_beats_go_out_while_a_cancellation_is_in_flight(self):
        """Stopping a container and waiting for a receipt take minutes.

        The loop turns throughout, which is what stops the Backend's sweeper
        deciding this machine went away while it was cancelling a Job.
        """
        release = threading.Event()
        started = threading.Event()
        self.addCleanup(release.set)

        def slow(job, terminal, outcome):
            started.set()
            release.wait(timeout=10)
            return cancelled()

        stop = Stop()
        clock = Clock()
        backend = FakeBackend(clock=clock, stop=stop, beats=4)
        socket = RegisteredSocket(backend, "worker-001")
        slot = holding()
        acceptance = JobAcceptance(
            run_job=lambda job, progress=None, terminal=None: FINISHED,
            cancel_job=slow, operator_address=OPERATOR)
        self.addCleanup(acceptance.close)
        acceptance.start_runtime(a_job())
        acceptance.start_cancellation(a_job(), USER_REQUEST)
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
        acceptance.terminal.put(cancelled())

        keep_alive(socket, stop, interval=HEARTBEAT_INTERVAL_SECONDS,
                   ack_timeout=HEARTBEAT_ACK_TIMEOUT_SECONDS, monotonic=clock,
                   on_message=lambda message: None,
                   each_turn=lambda: _every_turn(
                       acceptance, socket, slot, lambda *p: None))

        self.assertEqual(len(backend._of_type("JOB_CANCELLED")), 1)
        self.assertTrue(slot.is_idle)


class TestWhenNothingCanBeSaid(unittest.TestCase):
    def told(self, unsettled):
        from gshared_worker.job_acceptance import Decision

        lines: list = []
        _settle_cancellation(
            Decision(a_job(), None, Work.CANCEL, unsettled), holding(),
            lambda *p: lines.append(" ".join(str(x) for x in p)))
        return lines

    def test_a_job_that_ended_another_way_is_not_reported_as_a_problem(self):
        lines = self.told(Unsettled("123", ALREADY_ENDED, "owned by RESULT"))

        self.assertEqual(lines, [])

    def test_the_completion_race_is_named_for_the_operator(self):
        lines = self.told(Unsettled("123", BACKEND_CANCEL_COMPLETION_RACE,
                                    "the job completed first"))

        self.assertTrue(any("disagree" in line for line in lines), lines)

    def test_anything_else_says_the_job_is_being_kept(self):
        lines = self.told(Unsettled("123", "RECONCILIATION_REQUIRED",
                                    "the container is still running"))

        self.assertTrue(any("being held" in line for line in lines), lines)

    def test_the_slot_is_never_released_by_any_of_them(self):
        from gshared_worker.job_acceptance import Decision

        for unsettled in (Unsettled("123", ALREADY_ENDED, "x"),
                          Unsettled("123", BACKEND_CANCEL_COMPLETION_RACE, "y"),
                          Unsettled("123", "RECONCILIATION_REQUIRED", "z")):
            with self.subTest(reason=unsettled.reason):
                slot = holding()
                _settle_cancellation(
                    Decision(a_job(), None, Work.CANCEL, unsettled), slot,
                    lambda *p: None)
                self.assertFalse(slot.is_idle)


class TestOnlyTheConnectionSends(unittest.TestCase):
    def test_the_cancellation_layers_hold_no_socket(self):
        from gshared_worker import (
            job_acceptance,
            job_cancellation,
            runtime_stop,
        )

        for module in (job_cancellation, runtime_stop, job_acceptance):
            source = code_only(module)
            for absent in ("RegisteredSocket", "websocket", "socket.send",
                           "job_cancelled_terminal_message"):
                with self.subTest(module=module.__name__, absent=absent):
                    self.assertNotIn(absent, source)

    def test_nothing_runs_through_a_shell(self):
        from gshared_worker import job_cancellation, runtime_stop

        for module in (runtime_stop, job_cancellation):
            source = inspect.getsource(module)
            with self.subTest(module=module.__name__):
                self.assertNotIn("shell=True", source)
                self.assertNotIn("os.system", source)


if __name__ == "__main__":
    unittest.main()
