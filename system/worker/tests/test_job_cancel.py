"""Cleaning up after the Backend refuses a JOB_ACCEPTED.

The situation this handles is narrow and unpleasant. The Worker checked the
chain, signed ``acceptJob``, confirmed the job was ASSIGNED to it, and told the
Backend -- and the Backend did not believe the claim. The job will not run, but
**it is still assigned to this machine on chain**, so the client's deposit is
locked until the execution timeout expires. Twenty-four hours, on the deployed
settings.

So the Backend sends ``JOB_CANCEL`` with ``ACCEPT_VALIDATION_FAILED`` and the
Worker gives the job back with ``abortJob``.

Everything here is about not doing that too eagerly. The path ends in a
transaction that refunds somebody, so every test below is really the same
question: *is this definitely the job we hold, definitely still ours on chain,
and definitely in a state where giving it back is the right answer?*
"""

from __future__ import annotations

import json
import threading
import unittest

from gshared_worker.escrow import AcceptOutcome, AcceptResult, JobState
from gshared_worker.job_acceptance import JobAcceptance, Work
from gshared_worker.job_rejection import RejectCode
from gshared_worker.worker_connection import (
    HEARTBEAT_ACK_TIMEOUT_SECONDS,
    HEARTBEAT_INTERVAL_SECONDS,
    _dispatch_to,
    answer_decisions,
    cancel_the_job,
    keep_alive,
)
from gshared_worker.worker_job import (
    ACCEPT_VALIDATION_FAILED,
    USER_REQUEST,
    JobSlot,
    read_assignment,
)
from gshared_worker.worker_socket import RegisteredSocket

from tests.test_worker_connection import At, Clock, FakeBackend, Stop

OPERATOR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"

CANCELLED = AcceptResult(AcceptOutcome.ACCEPTED, "the job is cancelled",
                         "0xabortTx", state=JobState.CANCELLED)
ALREADY_CANCELLED = AcceptResult(AcceptOutcome.ACCEPTED,
                                 "the job was already cancelled",
                                 state=JobState.CANCELLED)
UNCERTAIN = AcceptResult(AcceptOutcome.UNCERTAIN, "the node stopped answering",
                         "0xabortTx", code=RejectCode.BLOCKCHAIN_RPC_FAILED)
COMPLETED = AcceptResult(AcceptOutcome.REFUSED, "escrow job 17 is COMPLETED",
                         code=RejectCode.INVALID_ESCROW_STATE,
                         state=JobState.COMPLETED)
STILL_FUNDED = AcceptResult(AcceptOutcome.REFUSED, "escrow job 17 is FUNDED",
                            code=RejectCode.INVALID_ESCROW_STATE,
                            state=JobState.FUNDED)
ANOTHER_OPERATOR = AcceptResult(
    AcceptOutcome.REFUSED, "accepted by another operator",
    code=RejectCode.OPERATOR_ADDRESS_MISMATCH, state=JobState.ASSIGNED)


def assignment(job_id="123"):
    return {
        "type": "JOB_ASSIGN", "jobId": job_id,
        "timestamp": "2026-09-11T01:00:00Z",
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


def cancellation(job_id="123", reason=ACCEPT_VALIDATION_FAILED, **changes):
    payload = {"reason": reason}
    payload.update(changes)
    return {"type": "JOB_CANCEL", "jobId": job_id,
            "timestamp": "2026-09-11T01:02:00Z", "payload": payload}


class Recorder:
    """A JobAcceptance whose cleanup is a stub, so no chain is involved."""

    def __init__(self, result=CANCELLED, busy=False):
        self.asked: list = []
        self._result = result
        self._busy = busy
        self.operator_address = OPERATOR

    def start_cleanup(self, job):
        self.asked.append(job.job_id)
        return not self._busy

    def start(self, job):
        return True

    def take_decisions(self):
        return []


def held_slot(job_id="123"):
    slot = JobSlot()
    slot.offer(a_job(job_id))
    slot.confirm()
    return slot


class TestOnlyOneReasonIsActedOn(unittest.TestCase):
    """The path ends in a refund. It does not run on a reason we cannot read."""

    def ask(self, message, slot=None):
        acceptance = Recorder()
        lines: list = []
        cancel_the_job(message, slot if slot is not None else held_slot(),
                       lambda *p: lines.append(" ".join(str(x) for x in p)),
                       acceptance)
        return acceptance, lines

    def test_accept_validation_failed_starts_a_cleanup(self):
        acceptance, _ = self.ask(cancellation())

        self.assertEqual(acceptance.asked, ["123"])

    def test_user_request_does_not(self):
        # Task 10's job. Aborting here would refund a client because somebody
        # pressed cancel, which is a decision nobody has made yet.
        acceptance, lines = self.ask(cancellation(reason=USER_REQUEST))

        self.assertEqual(acceptance.asked, [])
        self.assertTrue(any("does not act on that yet" in line for line in lines))

    def test_an_unknown_reason_does_not(self):
        acceptance, _ = self.ask(cancellation(reason="SOMETHING_NEW"))

        self.assertEqual(acceptance.asked, [])

    def test_a_missing_reason_is_not_defaulted_to_the_one_we_handle(self):
        # The dangerous default. It would make every cancellation -- including
        # an ordinary user one -- send an abortJob.
        message = {"type": "JOB_CANCEL", "jobId": "123",
                   "timestamp": "t", "payload": {}}
        acceptance, _ = self.ask(message)

        self.assertEqual(acceptance.asked, [])

    def test_a_cancellation_with_no_payload_is_refused(self):
        message = {"type": "JOB_CANCEL", "jobId": "123", "timestamp": "t"}
        acceptance, lines = self.ask(message)

        self.assertEqual(acceptance.asked, [])
        self.assertTrue(any("could not be read" in line for line in lines))

    def test_a_cancellation_with_no_job_id_is_refused(self):
        message = {"type": "JOB_CANCEL", "timestamp": "t",
                   "payload": {"reason": ACCEPT_VALIDATION_FAILED}}
        acceptance, _ = self.ask(message)

        self.assertEqual(acceptance.asked, [])

    def test_a_malformed_cancellation_never_drops_the_connection(self):
        # A Backend that learns to send something new must not be able to take
        # a working Worker offline by doing so.
        self.ask({"type": "JOB_CANCEL", "jobId": "123", "timestamp": "t",
                  "payload": "not an object"})


class TestItMustBeTheJobWeHold(unittest.TestCase):
    def ask(self, message, slot):
        acceptance = Recorder()
        lines: list = []
        cancel_the_job(message, slot,
                       lambda *p: lines.append(" ".join(str(x) for x in p)),
                       acceptance)
        return acceptance, lines

    def test_the_held_job_is_cleaned_up(self):
        acceptance, _ = self.ask(cancellation("123"), held_slot("123"))

        self.assertEqual(acceptance.asked, ["123"])

    def test_a_different_job_id_is_not(self):
        # The chain work is keyed by the escrow id of the job we hold. Acting
        # on a cancellation for another job would abort the wrong one.
        acceptance, lines = self.ask(cancellation("456"), held_slot("123"))

        self.assertEqual(acceptance.asked, [])
        self.assertTrue(any("456" in line and "123" in line for line in lines))

    def test_holding_nothing_means_nothing_to_release(self):
        acceptance, lines = self.ask(cancellation("123"), JobSlot())

        self.assertEqual(acceptance.asked, [])
        self.assertTrue(any("not holding it" in line for line in lines))

    def test_the_slot_is_kept_when_the_cleanup_cannot_start(self):
        # Busy, or no escrow configured. Giving the slot up while the chain may
        # still have this job assigned here would lose track of it.
        slot = held_slot("123")
        acceptance = Recorder(busy=True)
        lines: list = []
        cancel_the_job(cancellation("123"), slot,
                       lambda *p: lines.append(" ".join(str(x) for x in p)),
                       acceptance)

        self.assertFalse(slot.is_idle)
        self.assertTrue(any("not lost" in line for line in lines))


class TestWhatHappensWhenTheChainAnswers(unittest.TestCase):
    """One decision in, one set of consequences out."""

    def settle(self, result, job_id="123"):
        slot = held_slot(job_id)
        stop = Stop()
        clock = Clock()
        backend = FakeBackend(clock=clock, stop=stop, beats=1)
        socket = RegisteredSocket(backend, "worker-001")
        acceptance = _Finished(result, job_id)
        lines: list = []
        answer_decisions(acceptance, socket, slot,
                         lambda *p: lines.append(" ".join(str(x) for x in p)))
        return backend, slot, lines

    def test_a_cancelled_job_is_reported(self):
        backend, _, _ = self.settle(CANCELLED)

        self.assertEqual(len(backend.cancelleds), 1)

    def test_a_cancelled_job_frees_the_slot(self):
        _, slot, _ = self.settle(CANCELLED)

        self.assertTrue(slot.is_idle)

    def test_the_message_says_why_and_names_the_transaction(self):
        backend, _, _ = self.settle(CANCELLED)

        payload = backend.cancelleds[0]["payload"]
        self.assertEqual(payload["reason"], ACCEPT_VALIDATION_FAILED)
        self.assertEqual(payload["abortTxHash"], "0xabortTx")

    def test_a_job_already_cancelled_is_reported_without_a_transaction(self):
        # Reconciled rather than refused, and the field is omitted rather than
        # sent as null -- there is no transaction to point at.
        backend, slot, _ = self.settle(ALREADY_CANCELLED)

        self.assertEqual(len(backend.cancelleds), 1)
        self.assertNotIn("abortTxHash", backend.cancelleds[0]["payload"])
        self.assertTrue(slot.is_idle)

    def test_an_uncertain_abort_keeps_the_slot_and_says_nothing(self):
        # A transaction of ours may still land. Claiming a cancellation that
        # has not happened is worse than staying quiet.
        backend, slot, lines = self.settle(UNCERTAIN)

        self.assertEqual(backend.cancelleds, [])
        self.assertFalse(slot.is_idle)
        self.assertTrue(any("still lands" in line for line in lines))

    def test_a_completed_job_is_never_reported_as_cancelled(self):
        """The Backend says cancel; the chain says finished and paid.

        Both cannot be true. Nothing is sent and nothing is aborted -- a
        COMPLETED job has settled, and this machine is not the place to decide
        which side is wrong.
        """
        backend, slot, lines = self.settle(COMPLETED)

        self.assertEqual(backend.cancelleds, [])
        self.assertFalse(slot.is_idle)
        self.assertTrue(any("needs a person" in line for line in lines))

    def test_a_job_that_was_never_ours_frees_the_slot_quietly(self):
        # FUNDED means no acceptJob of ours ever landed. There is nothing to
        # give back, and holding the reservation would block real work.
        backend, slot, _ = self.settle(STILL_FUNDED)

        self.assertEqual(backend.cancelleds, [])
        self.assertTrue(slot.is_idle)

    def test_another_operators_job_is_held_rather_than_given_up(self):
        backend, slot, _ = self.settle(ANOTHER_OPERATOR)

        self.assertEqual(backend.cancelleds, [])
        self.assertFalse(slot.is_idle)


class _Finished:
    """An acceptance that has one cleanup decision waiting."""

    def __init__(self, result, job_id="123"):
        self._decisions = [_decision(result, job_id)]
        self.operator_address = OPERATOR

    def take_decisions(self):
        found, self._decisions = self._decisions, []
        return found


def _decision(result, job_id):
    from gshared_worker.job_acceptance import Decision

    return Decision(a_job(job_id), result, Work.CLEANUP)


class TestTheBackgroundThread(unittest.TestCase):
    """Same thread, same queue, same one-at-a-time rule as accepting."""

    def test_a_cleanup_runs_off_the_connection_thread(self):
        acceptance = JobAcceptance(clean_up=lambda job: CANCELLED,
                                   operator_address=OPERATOR)
        self.addCleanup(acceptance.close)

        self.assertTrue(acceptance.start_cleanup(a_job()))
        acceptance.close()

        decisions = acceptance.take_decisions()
        self.assertEqual(len(decisions), 1)
        self.assertTrue(decisions[0].is_cleanup)

    def test_a_cleanup_and_an_acceptance_do_not_run_together(self):
        # Two at once could have this machine accepting and aborting the same
        # job in the same second.
        held = threading.Event()
        started = threading.Event()

        def slow(job):
            started.set()
            held.wait(timeout=5)
            return CANCELLED

        acceptance = JobAcceptance(decide=slow, clean_up=slow,
                                   operator_address=OPERATOR)
        self.addCleanup(acceptance.close)
        self.addCleanup(held.set)

        self.assertTrue(acceptance.start(a_job()))
        started.wait(timeout=5)

        self.assertFalse(acceptance.start_cleanup(a_job()))
        held.set()

    def test_with_no_escrow_configured_there_is_nothing_to_clean_up(self):
        # Nothing was ever accepted on chain, so there is nothing to give back.
        acceptance = JobAcceptance()
        self.addCleanup(acceptance.close)

        self.assertFalse(acceptance.start_cleanup(a_job()))

    def test_a_cleaner_that_raised_is_uncertain_rather_than_a_refusal(self):
        def explode(job):
            raise RuntimeError("boom")

        acceptance = JobAcceptance(clean_up=explode, operator_address=OPERATOR)
        self.addCleanup(acceptance.close)

        acceptance.start_cleanup(a_job())
        acceptance.close()

        decision = acceptance.take_decisions()[0]
        self.assertIs(decision.result.outcome, AcceptOutcome.UNCERTAIN)
        self.assertTrue(decision.is_cleanup)


class TestTheHeartbeatKeepsGoing(unittest.TestCase):
    def test_beats_go_out_while_an_abort_is_in_flight(self):
        """An abortJob is a transaction and a receipt -- tens of seconds.

        The connection cannot be made to wait for it. The decider is released
        by the loop itself from inside ``each_turn``, so there is no polling
        and no race.
        """
        may_finish = threading.Event()
        stop = Stop()
        clock = Clock()
        backend = FakeBackend(At(5, json.dumps(cancellation())),
                              clock=clock, stop=stop, beats=4)
        socket = RegisteredSocket(backend, "worker-001")
        slot = held_slot("123")
        acceptance = JobAcceptance(
            clean_up=lambda job: CANCELLED if may_finish.wait(timeout=10)
            else UNCERTAIN,
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


if __name__ == "__main__":
    unittest.main()
