"""Deciding on a Job without stopping the connection that offered it.

The scenario that matters: an escrow acceptance takes forty seconds and the
heartbeat keeps going the whole time. Nothing here waits forty seconds -- the
decider is held on an Event the test releases when it has proved the point.
"""

from __future__ import annotations

import json
import threading
import unittest

from gshared_worker.escrow import AcceptOutcome, AcceptResult
from gshared_worker.job_rejection import RejectCode
from gshared_worker.job_acceptance import (
    Decision,
    JobAcceptance,
    refuse_without_escrow,
)
from gshared_worker.worker_connection import (
    HEARTBEAT_ACK_TIMEOUT_SECONDS,
    HEARTBEAT_INTERVAL_SECONDS,
    ConnectionOutcome,
    _dispatch_to,
    answer_decisions,
    keep_alive,
)
from gshared_worker.worker_job import JobSlot, SlotState, read_assignment
from gshared_worker.worker_socket import RegisteredSocket

from tests.test_worker_connection import (  # the fake Backend, clock and helper
    At,
    Clock,
    FakeBackend,
    Stop,
    code_only,
)


def assignment(job_id="123"):
    return {
        "type": "JOB_ASSIGN", "jobId": job_id,
        "timestamp": "2026-09-10T14:00:00+09:00",
        "payload": {
            "dockerImageId": 1, "modelId": 3, "datasetId": 42,
            "trainingMode": "AUTO",
            "trainingConfig": {"epochs": 3, "batchSize": 8,
                               "learningRate": 0.0002, "maxLength": 1024},
        "escrowJobId": "17",
        "externalRef": "0x" + "ab" * 32,
        "price": "9000000000000000000",
        "clientWallet": "0x1111111111111111111111111111111111111111",
        "providerWallet": "0x2222222222222222222222222222222222222222",
        },
    }


def a_job(job_id="123"):
    return read_assignment(assignment(job_id))


OPERATOR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"

ACCEPTED = AcceptResult(AcceptOutcome.ACCEPTED, "on chain", "0xtx")
REFUSED = AcceptResult(AcceptOutcome.REFUSED, "funded for somebody else",
                       code=RejectCode.PROVIDER_MISMATCH)
UNCERTAIN = AcceptResult(AcceptOutcome.UNCERTAIN, "the node stopped answering")


class TestDecidingInTheBackground(unittest.TestCase):
    def test_a_decision_comes_back_on_the_queue(self):
        acceptance = JobAcceptance(lambda job: ACCEPTED)
        self.addCleanup(acceptance.close)

        acceptance.start(a_job())
        acceptance.close()

        decisions = acceptance.take_decisions()
        self.assertEqual(len(decisions), 1)
        self.assertTrue(decisions[0].accepted)

    def test_the_decision_names_the_job_it_was_about(self):
        acceptance = JobAcceptance(lambda job: ACCEPTED)
        self.addCleanup(acceptance.close)

        acceptance.start(a_job("456"))
        acceptance.close()

        self.assertEqual(acceptance.take_decisions()[0].job.job_id, "456")

    def test_taking_decisions_twice_does_not_repeat_them(self):
        acceptance = JobAcceptance(lambda job: ACCEPTED)
        self.addCleanup(acceptance.close)

        acceptance.start(a_job())
        acceptance.close()
        acceptance.take_decisions()

        self.assertEqual(acceptance.take_decisions(), [])

    def test_taking_decisions_never_blocks(self):
        # The connection loop calls this between reads and has a beat to keep.
        acceptance = JobAcceptance()

        self.assertEqual(acceptance.take_decisions(), [])

    def test_a_decider_that_raised_is_not_read_as_a_refusal(self):
        # It told us nothing about the chain, so the Job is not given up.
        def explode(job):
            raise RuntimeError("boom")

        acceptance = JobAcceptance(explode)
        self.addCleanup(acceptance.close)

        acceptance.start(a_job())
        acceptance.close()

        self.assertIs(
            acceptance.take_decisions()[0].result.outcome, AcceptOutcome.UNCERTAIN)

    def test_only_one_decision_runs_at_a_time(self):
        held = threading.Event()
        started = threading.Event()

        def slow(job):
            started.set()
            held.wait(timeout=5)
            return ACCEPTED

        acceptance = JobAcceptance(slow)
        self.addCleanup(acceptance.close)
        self.addCleanup(held.set)

        self.assertTrue(acceptance.start(a_job("123")))
        started.wait(timeout=5)

        # The same Job again, and a different one. Neither may start a second
        # run -- that would be two acceptJob transactions.
        self.assertFalse(acceptance.start(a_job("123")))
        self.assertFalse(acceptance.start(a_job("456")))
        held.set()

    def test_it_says_what_it_is_deciding_on(self):
        held = threading.Event()
        started = threading.Event()

        def slow(job):
            started.set()
            held.wait(timeout=5)
            return ACCEPTED

        acceptance = JobAcceptance(slow)
        self.addCleanup(acceptance.close)
        self.addCleanup(held.set)

        acceptance.start(a_job("789"))
        started.wait(timeout=5)

        self.assertTrue(acceptance.busy)
        self.assertEqual(acceptance.deciding_on(), "789")
        held.set()

    def test_it_is_free_again_afterwards(self):
        acceptance = JobAcceptance(lambda job: ACCEPTED)
        self.addCleanup(acceptance.close)

        acceptance.start(a_job())
        acceptance.close()

        self.assertFalse(acceptance.busy)
        self.assertTrue(acceptance.start(a_job("456")))

    def test_no_thread_is_left_running(self):
        before = threading.active_count()
        acceptance = JobAcceptance(lambda job: ACCEPTED)

        acceptance.start(a_job())
        acceptance.close()

        self.assertLessEqual(threading.active_count(), before + 1)


class TestRefusingWithoutAnEscrow(unittest.TestCase):
    """What every installation does today: there is no escrow deployed.

    An earlier build accepted here, and that was right at the time -- a
    JOB_ASSIGN carried no on-chain fields, so there was nothing to check and
    nothing to be wrong about. Now it carries five, and answering "yes" to
    terms this machine has not read the chain for would be asserting something
    unverified to a Backend that starts the Job on hearing it.
    """

    def test_it_refuses(self):
        self.assertFalse(refuse_without_escrow(a_job()).accepted)

    def test_it_is_a_settled_no_rather_than_an_unknown(self):
        # The slot can be given back: nothing was signed and nothing is in
        # flight, so holding the reservation would help nobody.
        self.assertIs(refuse_without_escrow(a_job()).outcome,
                      AcceptOutcome.REFUSED)

    def test_it_says_why_in_a_code_the_backend_can_act_on(self):
        self.assertIs(refuse_without_escrow(a_job()).code,
                      RejectCode.ESCROW_NOT_CONFIGURED)

    def test_that_code_is_not_worth_retrying(self):
        # Nothing about this machine will change in a minute.
        self.assertFalse(refuse_without_escrow(a_job()).transient)

    def test_it_is_the_default(self):
        # The point of the change: a Worker that was never configured refuses
        # rather than accepting, without anybody having to remember to wire it.
        acceptance = JobAcceptance()
        self.addCleanup(acceptance.close)

        acceptance.start(a_job())
        acceptance.close()

        self.assertIs(acceptance.take_decisions()[0].result.code,
                      RejectCode.ESCROW_NOT_CONFIGURED)

    def test_it_touches_no_chain(self):
        from gshared_worker import job_acceptance

        source = code_only(job_acceptance)
        self.assertNotIn("web3", source)
        self.assertNotIn("Web3Escrow", source)


class TestTheConnectionKeepsGoing(unittest.TestCase):
    """A Worker must not go offline because it is busy accepting work."""

    def run_with(self, *scheduled, decide=None, slot=None, **kwargs):
        stop = Stop()
        clock = Clock()
        backend = FakeBackend(*scheduled, clock=clock, stop=stop, **kwargs)
        socket = RegisteredSocket(backend, "worker-001")
        slot = slot if slot is not None else JobSlot()
        acceptance = JobAcceptance(decide, operator_address=OPERATOR)
        lines = []
        say = lambda *p: lines.append(" ".join(str(x) for x in p))
        try:
            outcome, _ = keep_alive(
                socket, stop, interval=HEARTBEAT_INTERVAL_SECONDS,
                ack_timeout=HEARTBEAT_ACK_TIMEOUT_SECONDS, monotonic=clock,
                on_message=_dispatch_to(socket, slot, say, acceptance),
                each_turn=lambda: answer_decisions(acceptance, socket, slot, say))
        finally:
            acceptance.close()
        return outcome, backend, slot, lines

    def test_heartbeats_go_out_while_a_decision_is_in_flight(self):
        """Ninety seconds of beating with an acceptance stuck in the middle.

        The decider blocks until the **loop itself** has sent three beats, so
        there is no polling and no race: the loop releases it from inside
        ``each_turn``. In real time this would be a minute and a half of a
        Worker staying online while a transaction confirms.
        """
        may_finish = threading.Event()
        stop = Stop()
        clock = Clock()
        backend = FakeBackend(At(5, json.dumps(assignment())),
                              clock=clock, stop=stop, beats=4)
        socket = RegisteredSocket(backend, "worker-001")
        slot = JobSlot()
        acceptance = JobAcceptance(
            lambda job: ACCEPTED if may_finish.wait(timeout=10) else UNCERTAIN,
            operator_address=OPERATOR)
        lines = []
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

        # Three beats went out and were answered while the acceptance was
        # blocked -- the connection never stalled behind it.
        self.assertGreaterEqual(len(backend.heartbeats), 3)
        self.assertTrue(may_finish.is_set())

    def test_the_reply_waits_for_the_decision(self):
        # Nothing goes to the Backend until the acceptance says yes. Telling it
        # sooner would move a Job to RUNNING with no on-chain claim behind it.
        _, backend, _, _ = self.run_with(
            At(5, json.dumps(assignment())), decide=lambda job: REFUSED)

        self.assertEqual(backend.accepteds, [])

    def test_an_accepted_job_is_replied_to(self):
        _, backend, slot, _ = self.run_with(
            At(5, json.dumps(assignment())), decide=lambda job: ACCEPTED)

        self.assertEqual(len(backend.accepteds), 1)
        self.assertIs(slot.state, SlotState.ASSIGNED)

    def test_a_refused_job_frees_the_slot(self):
        _, _, slot, lines = self.run_with(
            At(5, json.dumps(assignment())), decide=lambda job: REFUSED)

        self.assertTrue(slot.is_idle)
        self.assertTrue(any("not accepted" in line for line in lines))

    def test_an_uncertain_job_is_kept_rather_than_given_up(self):
        # A transaction of ours may still land. Freeing the slot now would
        # have this machine owing two Jobs.
        _, _, slot, lines = self.run_with(
            At(5, json.dumps(assignment())), decide=lambda job: UNCERTAIN)

        self.assertFalse(slot.is_idle)
        self.assertIs(slot.state, SlotState.ACCEPTING)
        self.assertTrue(any("held rather than given up" in line
                            for line in lines))

    def test_a_second_job_is_not_taken_while_one_is_being_decided(self):
        _, backend, slot, lines = self.run_with(
            At(5, json.dumps(assignment("123"))),
            At(6, json.dumps(assignment("456"))),
            decide=lambda job: UNCERTAIN)

        self.assertEqual(slot.held.job_id, "123")
        self.assertTrue(any("456" in line and "not accepted" in line
                            for line in lines))

    def test_the_same_job_offered_again_does_not_start_a_second_decision(self):
        decided = []

        def count(job):
            decided.append(job.job_id)
            return ACCEPTED

        _, backend, _, _ = self.run_with(
            At(5, json.dumps(assignment("123"))),
            At(5.1, json.dumps(assignment("123"))),
            decide=count)

        self.assertEqual(decided, ["123"])

    def test_a_settled_job_offered_again_is_replied_to_without_deciding(self):
        decided = []

        def count(job):
            decided.append(job.job_id)
            return ACCEPTED

        _, backend, _, _ = self.run_with(
            At(5, json.dumps(assignment("123"))),
            At(20, json.dumps(assignment("123"))),
            decide=count)

        self.assertEqual(decided, ["123"])
        self.assertEqual(len(backend.accepteds), 2)


class TestOnlyTheLoopTouchesTheSocket(unittest.TestCase):
    def source(self):
        # Docstrings stripped: this module explains at length that it never
        # touches the socket, and the words it explains it with are the words
        # being searched for.
        from gshared_worker import job_acceptance

        return code_only(job_acceptance)

    def test_the_decider_never_receives(self):
        for absent in ("recv", "receive"):
            self.assertNotIn(absent, self.source())

    def test_the_decider_never_sends(self):
        for absent in ("socket", "RegisteredSocket", ".send("):
            self.assertNotIn(absent, self.source())

    def test_it_needs_no_lock_because_it_shares_no_socket(self):
        # There is one Lock, and it guards which Job is being decided on --
        # not a connection.
        source = self.source()

        self.assertEqual(source.count("Lock()"), 1)


if __name__ == "__main__":
    unittest.main()
