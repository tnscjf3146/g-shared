"""A training run takes hours. The connection has a beat to keep.

This is the same arrangement escrow acceptance uses, and it exists for the
same reason: the WebSocket loop is the only thread that reads the socket and
the only one that sends heartbeats, so anything that can block for more than
a moment happens somewhere else and answers on a queue.

The failure this prevents is specific and quiet. A Worker that ran the
container on the receive loop would stop beating; the Backend's sweeper would
mark it offline after ninety seconds; the Job would be reassigned -- while the
container was still training it.
"""

from __future__ import annotations

import ast
import inspect
import json
import threading
import unittest

from gshared_worker.escrow import AcceptOutcome, AcceptResult
from gshared_worker.job_acceptance import JobAcceptance, Work
from gshared_worker.job_runtime import (
    RuntimeFailure,
    RuntimeOutcome,
    RuntimeState,
)
from gshared_worker.worker_connection import (
    HEARTBEAT_ACK_TIMEOUT_SECONDS,
    HEARTBEAT_INTERVAL_SECONDS,
    _dispatch_to,
    answer_decisions,
    keep_alive,
)
from gshared_worker.worker_job import JobSlot, read_assignment
from gshared_worker.worker_socket import RegisteredSocket

from tests.test_worker_connection import At, Clock, FakeBackend, Stop

OPERATOR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"

FINISHED = RuntimeOutcome(RuntimeState.FINISHED, None, "training finished", 0)
FAILED = RuntimeOutcome(RuntimeState.FAILED, RuntimeFailure.CUDA_OOM,
                        "the GPU ran out of memory", 13)


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


class TestOneRuntimeAtATime(unittest.TestCase):
    """One GPU, one container. Two would share a card and an output directory."""

    def test_a_runtime_runs_in_the_background(self):
        acceptance = JobAcceptance(run_job=lambda job: FINISHED,
                                   operator_address=OPERATOR)
        self.addCleanup(acceptance.close)

        self.assertTrue(acceptance.start_runtime(a_job()))
        acceptance.close()

        decisions = acceptance.take_decisions()
        self.assertEqual(len(decisions), 1)
        self.assertIs(decisions[0].work, Work.RUN)
        self.assertTrue(decisions[0].runtime.succeeded)

    def test_a_second_start_for_the_same_job_is_refused(self):
        held = threading.Event()
        started = threading.Event()

        def slow(job):
            started.set()
            held.wait(timeout=5)
            return FINISHED

        acceptance = JobAcceptance(run_job=slow, operator_address=OPERATOR)
        self.addCleanup(acceptance.close)
        self.addCleanup(held.set)

        self.assertTrue(acceptance.start_runtime(a_job("123")))
        started.wait(timeout=5)

        self.assertFalse(acceptance.start_runtime(a_job("123")))
        self.assertFalse(acceptance.start_runtime(a_job("456")))
        held.set()

    def test_a_runtime_and_an_acceptance_do_not_run_together(self):
        held = threading.Event()
        started = threading.Event()

        def slow(job):
            started.set()
            held.wait(timeout=5)
            return AcceptResult(AcceptOutcome.ACCEPTED, "on chain")

        acceptance = JobAcceptance(decide=slow, run_job=lambda job: FINISHED,
                                   operator_address=OPERATOR)
        self.addCleanup(acceptance.close)
        self.addCleanup(held.set)

        self.assertTrue(acceptance.start(a_job()))
        started.wait(timeout=5)

        self.assertFalse(acceptance.start_runtime(a_job()))
        held.set()

    def test_with_no_runtime_configured_nothing_starts(self):
        # What every build before this one did: the Job is simply held.
        acceptance = JobAcceptance(operator_address=OPERATOR)
        self.addCleanup(acceptance.close)

        self.assertFalse(acceptance.start_runtime(a_job()))

    def test_a_pipeline_that_raised_still_answers(self):
        """A Job with no answer would hold the slot until the process ended."""
        def explode(job):
            raise RuntimeError("boom")

        acceptance = JobAcceptance(run_job=explode, operator_address=OPERATOR)
        self.addCleanup(acceptance.close)

        acceptance.start_runtime(a_job())
        acceptance.close()

        decision = acceptance.take_decisions()[0]
        self.assertIs(decision.work, Work.RUN)
        self.assertFalse(decision.runtime.succeeded)

    def test_it_is_free_again_afterwards(self):
        acceptance = JobAcceptance(run_job=lambda job: FINISHED,
                                   operator_address=OPERATOR)
        self.addCleanup(acceptance.close)

        acceptance.start_runtime(a_job())
        acceptance.close()

        self.assertFalse(acceptance.busy)
        self.assertTrue(acceptance.start_runtime(a_job("456")))

    def test_no_thread_is_left_running(self):
        before = threading.active_count()
        acceptance = JobAcceptance(run_job=lambda job: FINISHED,
                                   operator_address=OPERATOR)

        acceptance.start_runtime(a_job())
        acceptance.close()

        self.assertLessEqual(threading.active_count(), before + 1)


class TestTheHeartbeatKeepsGoing(unittest.TestCase):
    def test_beats_go_out_while_a_runtime_is_in_flight(self):
        """Ninety seconds of beating with a training run stuck in the middle.

        The pipeline blocks until the **loop itself** has sent three beats, so
        there is no polling and no race: the loop releases it from inside
        ``each_turn``. In real time this is a Worker staying online while a
        container trains.
        """
        may_finish = threading.Event()
        stop = Stop()
        clock = Clock()
        backend = FakeBackend(At(5, json.dumps(assignment())),
                              clock=clock, stop=stop, beats=4)
        socket = RegisteredSocket(backend, "worker-001")
        slot = JobSlot()
        acceptance = JobAcceptance(
            decide=lambda job: AcceptResult(AcceptOutcome.ACCEPTED, "ok", "0xtx"),
            run_job=lambda job: FINISHED if may_finish.wait(timeout=10) else FAILED,
            operator_address=OPERATOR)
        lines: list = []
        say = lambda *p: lines.append(" ".join(str(x) for x in p))

        def each_turn():
            if len(backend.heartbeats) >= 3:
                may_finish.set()
            answer_decisions(acceptance, socket, slot, say)

        try:
            keep_alive(socket, stop, interval=HEARTBEAT_INTERVAL_SECONDS,
                       ack_timeout=HEARTBEAT_ACK_TIMEOUT_SECONDS, monotonic=clock,
                       on_message=_dispatch_to(socket, slot, say, acceptance),
                       each_turn=each_turn)
        finally:
            may_finish.set()
            acceptance.close()

        self.assertGreaterEqual(len(backend.heartbeats), 3)


class TestTheSocketHasOneOwner(unittest.TestCase):
    def test_the_pipeline_never_touches_a_socket(self):
        from gshared_worker import job_runtime, runtime_pipeline

        for module in (runtime_pipeline, job_runtime):
            source = code_only(module)
            for absent in ("RegisteredSocket", "recv", "websocket",
                           "heartbeat_message"):
                with self.subTest(module=module.__name__, absent=absent):
                    self.assertNotIn(absent, source)

    def test_no_extra_receiver_thread_is_started(self):
        from gshared_worker import worker_connection

        source = code_only(worker_connection)
        self.assertNotIn("threading.Thread", source)

    def test_the_pipeline_sends_no_terminal_message(self):
        """The runtime returns a value; something else decides what to say.

        Task 9 reads that value, settles the Job and hands an answer to the
        connection loop. None of it happens in here.
        """
        from gshared_worker import job_runtime, runtime_pipeline

        for module in (runtime_pipeline, job_runtime):
            source = code_only(module)
            for absent in ("JOB_PROGRESS", "JOB_RESULT", "JOB_FAILED",
                           "job_failed_message", "job_result_message"):
                with self.subTest(module=module.__name__, absent=absent):
                    self.assertNotIn(absent, source)

    def test_the_settlement_sends_nothing_either(self):
        """Storage and the chain are its business. The socket is not.

        This is the same rule one stage later: the settlement blocks for as
        long as an upload and a transaction take, and a module that could
        send would be a second writer on a connection that has one.
        """
        from gshared_worker import job_settlement, result_upload

        for module in (job_settlement, result_upload):
            source = code_only(module)
            for absent in ("RegisteredSocket", "websocket", "socket.send",
                           "JOB_RESULT", "JOB_FAILED", "job_result_message",
                           "job_failed_message", "heartbeat"):
                with self.subTest(module=module.__name__, absent=absent):
                    self.assertNotIn(absent, source)


class TestWhatTheSupplierIsTold(unittest.TestCase):
    def settle(self, outcome, acceptance=None):
        from gshared_worker.job_acceptance import Decision
        from gshared_worker.worker_connection import _settle_runtime

        slot = JobSlot()
        slot.offer(a_job())
        slot.confirm()
        lines: list = []
        _settle_runtime(
            Decision(a_job(), AcceptResult(AcceptOutcome.ACCEPTED, "x"),
                     Work.RUN, outcome),
            slot, lambda *p: lines.append(" ".join(str(x) for x in p)),
            acceptance)
        return slot, lines

    def test_a_finished_run_is_reported(self):
        _, lines = self.settle(FINISHED)

        self.assertTrue(any("학습을 마쳤습니다" in line for line in lines))

    def test_a_failed_run_names_the_reason(self):
        _, lines = self.settle(FAILED)

        self.assertTrue(any("CUDA_OOM" in line for line in lines))

    def test_the_job_is_kept_either_way(self):
        """The container stopping is not the end of the Job.

        The result still has to be stored and the escrow settled, and the
        slot is this Worker's claim on the Job while that happens. Giving it
        back now would let a second Job overwrite the workspace before
        anybody had read it.
        """
        for outcome in (FINISHED, FAILED):
            with self.subTest(outcome=outcome.state):
                slot, _ = self.settle(outcome)
                self.assertFalse(slot.is_idle)

    def test_the_supplier_is_told_when_settlement_is_not_configured(self):
        # Silence here would look exactly like a result that had been sent.
        for outcome in (FINISHED, FAILED):
            with self.subTest(outcome=outcome.state):
                _, lines = self.settle(outcome)
                self.assertTrue(
                    any("동작하지 않습니다" in line
                        for line in lines), lines)

    def test_a_configured_settlement_is_started_instead(self):
        started: list = []

        class Acceptance:
            def start_settlement(self, job, outcome):
                started.append((job.job_id, outcome))
                return True

        for outcome in (FINISHED, FAILED):
            with self.subTest(outcome=outcome.state):
                started.clear()
                slot, lines = self.settle(outcome, Acceptance())

                self.assertEqual(len(started), 1)
                self.assertFalse(slot.is_idle, "the slot was given up early")
                self.assertFalse(
                    any("동작하지 않습니다" in line
                        for line in lines), lines)


if __name__ == "__main__":
    unittest.main()
