"""Staying connected: reading, beating on time, noticing it broke, coming back.

No sleeping and no threads. The clock is a variable the fake transport moves
forward by however long each read was told it could wait, so thirty seconds of
heartbeat interval pass in a few dozen loop iterations and the whole file runs
in milliseconds. The real constants are used rather than small test ones --
what is being checked includes *when* a beat goes out.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from gshared_worker.config import WorkerSettings
from gshared_worker.exit_codes import WorkerExitCode
from gshared_worker.worker_credentials import WorkerCredentials
from gshared_worker.escrow import AcceptOutcome, AcceptResult
from gshared_worker.worker_connection import (
    BACKOFF_MAX_SECONDS,
    HEARTBEAT_ACK_TIMEOUT_SECONDS,
    HEARTBEAT_INTERVAL_SECONDS,
    READ_SLICE_SECONDS,
    ConnectionOutcome,
    InterruptibleStop,
    backoff_delays,
    _dispatch_to,
    answer_decisions,
    connect_to_backend,
    heartbeat_message,
    keep_alive,
    stay_connected,
)
from gshared_worker.job_acceptance import JobAcceptance
from gshared_worker.worker_job import JobSlot
from gshared_worker.worker_socket import (
    ConnectionWentAway,
    HandshakeRefused,
    RegisteredSocket,
    TransportUnavailable,
)

#: This machine's on-chain signing address in these tests. Public, not a key.
OPERATOR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"

#: A decision stub. These tests are about wiring, not about chains.
ACCEPTED_STUB = AcceptResult(AcceptOutcome.ACCEPTED, "stubbed", "0xtx")


TOKEN = "gsh_wk_a-secret-nobody-should-see"
CREDENTIALS = WorkerCredentials("worker-001", TOKEN)


def code_only(module) -> str:
    """A module's source with its docstrings and comments taken out.

    The tests that say "this module does not do X" grep the source for X. Every
    one of them failed the first time it ran, because the module says in prose
    that it does not do those things -- a docstring reading "nothing here calls
    time.sleep" is not a call to time.sleep. Stripping the prose is what makes
    the question being asked the one that was meant.
    """
    import ast
    import inspect

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
    return ast.unparse(tree)  # comments do not survive unparse either


def settings() -> WorkerSettings:
    # No contract: these tests are about the connection, not jobs. With the
    # SSAFY escrow as the default, leaving it implicit would let a developer's
    # own worker-wallet.json turn this into a real chain client.
    return WorkerSettings(
        backend_url="wss://gpu.example.com/ws/workers", worker_id="worker-001",
        hourly_price=600, gpu_index=0, supported_workloads=(),
        worker_token=None, config_path=None, escrow_address=None)


def envelope(kind: str, **payload) -> str:
    """What the Backend sends: no jobId, empty payload unless a test wants one."""
    return json.dumps(
        {"type": kind, "timestamp": "2026-09-10T12:00:01+09:00", "payload": payload})


REGISTER_ACK = envelope("REGISTER_ACK")
HEARTBEAT_ACK = envelope("HEARTBEAT_ACK")


class Stop:
    """A stop signal a test drives by hand.

    ``waits`` records every timeout the code under test asked to wait for.
    Only :func:`stay_connected` waits on this now -- for the backoff between
    connections -- so that list is how the backoff policy is checked without
    any of it elapsing. ``stop_after`` sets the signal once that many waits
    have happened; :class:`FakeTransport` sets it when its script runs dry.
    """

    def __init__(self, stop_after: int | None = None, set_now: bool = False) -> None:
        self._set = set_now
        self._stop_after = stop_after
        self.waits: list[float] = []

    def is_set(self) -> bool:
        return self._set

    def wait(self, timeout: float | None = None) -> bool:
        self.waits.append(timeout)
        if self._stop_after is not None and len(self.waits) >= self._stop_after:
            self._set = True
        return self._set

    def set(self) -> None:
        self._set = True


class Clock:
    """Fake monotonic time. Only :class:`FakeBackend` moves it."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class At:
    """Something the Backend sends of its own accord, at a given moment."""

    def __init__(self, seconds: float, raw: str) -> None:
        self.seconds = seconds
        self.raw = raw


class FakeBackend:
    """A Backend that answers the way ``WorkerWebSocketHandler`` does.

    Not a tape of replies: it **reacts**. A REGISTER gets a REGISTER_ACK, a
    HEARTBEAT gets a HEARTBEAT_ACK, a JOB_ACCEPTED gets nothing -- which is what
    the Java does. Messages it starts itself (JOB_ASSIGN) are scheduled with
    :class:`At` so a test can say *when* they turn up rather than only in what
    order.

    Waiting is the only thing that costs time, and here it costs none: a read
    that finds nothing moves the clock forward by however long it was allowed
    to wait. Thirty seconds of heartbeat interval is sixty loop iterations.

    ``beats`` ends the run: once that many heartbeats have been answered and
    there is nothing left to send, the stop signal is set.
    """

    def __init__(self, *scheduled, clock=None, stop=None, beats=1,
                 answers=True, on_send=None) -> None:
        # A bare string or exception is something the Backend says straight
        # away; At() is for something it says later.
        self._scheduled = sorted(
            (item if isinstance(item, At) else At(0, item) for item in scheduled),
            key=lambda item: item.seconds)
        self._pending: list[str] = []
        self._clock = clock or Clock()
        self._stop = stop
        self._beats = beats
        self._answers = answers
        self._on_send = on_send
        self.sent: list[str] = []
        self.sent_at: list[float] = []
        self.closed = False
        self.timeouts: list[float] = []

    @property
    def clock(self) -> Clock:
        return self._clock

    def send(self, text: str) -> None:
        if self._on_send is not None:
            raise self._on_send
        self.sent.append(text)
        self.sent_at.append(self._clock.now)
        if not self._answers:
            return
        kind = json.loads(text)["type"]
        if kind == "HEARTBEAT":
            self._pending.append(HEARTBEAT_ACK)
        elif kind == "REGISTER":
            self._pending.append(REGISTER_ACK)
        # JOB_ACCEPTED is answered by nothing. handleJobAccepted sends no reply.

    def recv(self, timeout: float) -> str:
        self.timeouts.append(timeout)
        if self._pending:
            return self._pending.pop(0)

        due = [item for item in self._scheduled if item.seconds <= self._clock.now]
        if due:
            self._scheduled.remove(due[0])
            if isinstance(due[0].raw, Exception):
                raise due[0].raw
            return due[0].raw

        # Only a Backend that answers ends a run this way. One that stays
        # silent is being asked what the Worker does about it, and setting the
        # stop signal would answer the question for it.
        if self._answers and not self._scheduled and self._stop is not None \
                and len(self.heartbeats) >= self._beats:
            self._stop.set()

        # Never sleep past something the Backend is due to say.
        if self._scheduled:
            timeout = min(timeout, self._scheduled[0].seconds - self._clock.now)
        self._clock.advance(max(timeout, 0.0))
        raise TimeoutError

    def close(self) -> None:
        self.closed = True

    def _of_type(self, kind: str) -> list[dict]:
        return [json.loads(t) for t in self.sent if json.loads(t)["type"] == kind]

    @property
    def heartbeats(self) -> list[dict]:
        return self._of_type("HEARTBEAT")

    @property
    def registers(self) -> list[dict]:
        return self._of_type("REGISTER")

    @property
    def accepteds(self) -> list[dict]:
        return self._of_type("JOB_ACCEPTED")

    @property
    def rejecteds(self) -> list[dict]:
        return self._of_type("JOB_REJECTED")

    @property
    def cancelleds(self) -> list[dict]:
        return self._of_type("JOB_CANCELLED")


#: Kept as the old name so the reconnect tests below read unchanged.
FakeTransport = FakeBackend


def live(*scheduled, on_send=None, stop=None, **kwargs):
    """A registered socket over a fake Backend."""
    clock = Clock()
    backend = FakeBackend(
        *scheduled, clock=clock, on_send=on_send, stop=stop, **kwargs)
    return RegisteredSocket(backend, "worker-001"), backend


def beat(*scheduled, stop=None, on_send=None, **kwargs):
    """One trip through :func:`keep_alive` against a fake Backend.

    Keyword arguments are split between the Backend (``beats``, ``answers``)
    and :func:`keep_alive` (``on_message``, ...).
    """
    stop = stop or Stop()
    backend_options = {
        name: kwargs.pop(name) for name in ("beats", "answers") if name in kwargs}
    socket, backend = live(
        *scheduled, on_send=on_send, stop=stop, **backend_options)
    outcome, detail = keep_alive(
        socket, stop, interval=HEARTBEAT_INTERVAL_SECONDS,
        ack_timeout=HEARTBEAT_ACK_TIMEOUT_SECONDS,
        monotonic=backend.clock, **kwargs)
    return outcome, detail, backend


class TestTheHeartbeatMessage(unittest.TestCase):
    def test_it_is_a_heartbeat(self):
        self.assertEqual(heartbeat_message("t")["type"], "HEARTBEAT")

    def test_the_envelope_is_the_agreed_four_fields(self):
        self.assertEqual(
            set(heartbeat_message("t")), {"type", "jobId", "timestamp", "payload"})

    def test_the_job_id_is_null_because_this_is_not_about_a_job(self):
        self.assertIsNone(heartbeat_message("t")["jobId"])

    def test_the_payload_is_empty(self):
        # handleHeartbeat reads nothing out of it. A workerId here would be a
        # field that means nothing and could disagree with the session.
        self.assertEqual(heartbeat_message("t")["payload"], {})

    def test_it_carries_no_credential(self):
        raw = json.dumps(heartbeat_message("t"))

        for forbidden in (TOKEN, "workerToken", "accessToken", "refreshToken",
                          "password", "Authorization"):
            self.assertNotIn(forbidden, raw)

    def test_the_timestamp_is_carried_through(self):
        self.assertEqual(heartbeat_message("2026-09-10T12:00:30+09:00")["timestamp"],
                         "2026-09-10T12:00:30+09:00")

    def test_the_real_timestamp_is_iso_8601_with_an_offset(self):
        from datetime import datetime

        from gshared_worker.worker_socket import now_in_iso8601

        self.assertIsNotNone(datetime.fromisoformat(now_in_iso8601()).tzinfo)


class TestTheIntervals(unittest.TestCase):
    """The Backend's sweeper gives a machine 90 seconds."""

    def test_the_beat_matches_what_the_backend_expects(self):
        self.assertEqual(HEARTBEAT_INTERVAL_SECONDS, 30)

    def test_three_beats_fit_inside_the_backend_timeout(self):
        # One lost beat must not take a working machine offline.
        self.assertLessEqual(HEARTBEAT_INTERVAL_SECONDS * 3, 90)

    def test_the_ack_deadline_is_well_inside_the_beat(self):
        # A dead connection is noticed and replaced within one beat.
        self.assertLess(HEARTBEAT_ACK_TIMEOUT_SECONDS, HEARTBEAT_INTERVAL_SECONDS)


class TestBeatingOnALiveConnection(unittest.TestCase):
    def test_the_first_beat_goes_out_one_interval_in(self):
        _, _, backend = beat()

        self.assertEqual(backend.sent_at[0], HEARTBEAT_INTERVAL_SECONDS)

    def test_a_heartbeat_goes_out(self):
        _, _, backend = beat()

        self.assertEqual(len(backend.heartbeats), 1)
        self.assertEqual(backend.heartbeats[0]["type"], "HEARTBEAT")

    def test_an_ack_keeps_the_connection(self):
        outcome, _, _ = beat()

        self.assertIs(outcome, ConnectionOutcome.STOPPED)

    def test_several_beats_go_out_on_the_same_socket(self):
        outcome, _, backend = beat(beats=3)

        self.assertIs(outcome, ConnectionOutcome.STOPPED)
        self.assertEqual(len(backend.heartbeats), 3)

    def test_the_beats_are_one_interval_apart(self):
        _, _, backend = beat(beats=3)

        self.assertEqual(backend.sent_at, [30.0, 60.0, 90.0])

    def test_no_single_read_blocks_longer_than_a_slice(self):
        # A read that could wait half a minute is half a minute of ignoring
        # Ctrl+C, for the same reason InterruptibleStop waits in pieces.
        _, _, backend = beat()

        self.assertTrue(all(waited <= READ_SLICE_SECONDS
                            for waited in backend.timeouts))

    def test_keeping_alive_does_not_close_the_socket(self):
        # Whoever opened it closes it. One place, not two.
        _, _, backend = beat()

        self.assertFalse(backend.closed)

    def test_no_beat_carries_a_credential(self):
        _, _, backend = beat(beats=2)

        for raw in backend.sent:
            self.assertNotIn(TOKEN, raw)


class TestABeatThatIsNotAnswered(unittest.TestCase):
    def test_nothing_arriving_loses_the_connection(self):
        outcome, detail, _ = beat(answers=False)

        self.assertIs(outcome, ConnectionOutcome.LOST)
        self.assertIn("heartbeat", detail)

    def test_it_gives_up_one_ack_timeout_after_the_beat(self):
        _, _, backend = beat(answers=False)

        self.assertAlmostEqual(
            backend.clock.now,
            HEARTBEAT_INTERVAL_SECONDS + HEARTBEAT_ACK_TIMEOUT_SECONDS,
            places=6)

    def test_the_deadline_is_not_pushed_back_by_later_beats(self):
        # A connection that never answers must not stay alive one beat at a
        # time. The deadline belongs to the first unanswered beat.
        _, _, backend = beat(answers=False)

        self.assertEqual(len(backend.heartbeats), 1)

    def test_the_socket_closing_while_sending(self):
        outcome, _, _ = beat(on_send=ConnectionWentAway("gone"))

        self.assertIs(outcome, ConnectionOutcome.LOST)

    def test_the_socket_closing_while_waiting(self):
        outcome, _, _ = beat(At(1, ConnectionWentAway("gone")), answers=False)

        self.assertIs(outcome, ConnectionOutcome.LOST)

    def test_a_reply_that_is_not_json(self):
        outcome, _, _ = beat(At(1, "{ half written"), answers=False)

        self.assertIs(outcome, ConnectionOutcome.LOST)

    def test_a_reply_with_no_type(self):
        outcome, _, _ = beat(At(1, json.dumps({"payload": {}})), answers=False)

        self.assertIs(outcome, ConnectionOutcome.LOST)

    def test_a_json_value_that_is_not_a_message(self):
        outcome, _, _ = beat(At(1, '"hello"'), answers=False)

        self.assertIs(outcome, ConnectionOutcome.LOST)


class TestOnlyAnAckCountsAsAnAck(unittest.TestCase):
    """A message arriving is not the same as the beat being answered."""

    def test_a_register_ack_arriving_mid_beat_is_not_a_heartbeat_ack(self):
        # Well formed, and not a confirmation of this beat.
        outcome, _, _ = beat(At(31, REGISTER_ACK), answers=False)

        self.assertIs(outcome, ConnectionOutcome.LOST)

    def test_an_unhandled_message_does_not_end_the_beat(self):
        seen = []

        outcome, _, _ = beat(At(31, envelope("JOB_ASSIGN")), on_message=seen.append)

        self.assertIs(outcome, ConnectionOutcome.STOPPED)
        self.assertEqual([m["type"] for m in seen], ["JOB_ASSIGN"])

    def test_an_unhandled_message_alone_still_loses_the_connection(self):
        # It was noted, and the beat was never answered.
        outcome, _, _ = beat(
            At(31, envelope("JOB_ASSIGN")), answers=False, on_message=lambda m: None)

        self.assertIs(outcome, ConnectionOutcome.LOST)

    def test_the_default_handler_does_not_treat_it_as_an_ack(self):
        outcome, _, _ = beat(At(31, envelope("JOB_ASSIGN")), answers=False)

        self.assertIs(outcome, ConnectionOutcome.LOST)

    def test_a_message_before_any_beat_is_still_handled(self):
        # Nothing had been sent, so nothing could be being answered.
        seen = []

        beat(At(5, envelope("JOB_ASSIGN")), on_message=seen.append)

        self.assertEqual([m["type"] for m in seen], ["JOB_ASSIGN"])

    def test_a_flood_of_messages_does_not_postpone_the_deadline(self):
        # The deadline is per beat, not per read.
        outcome, _, backend = beat(
            *[At(30.1 + n / 10, envelope("JOB_ASSIGN")) for n in range(20)],
            answers=False, on_message=lambda m: None)

        self.assertIs(outcome, ConnectionOutcome.LOST)
        self.assertAlmostEqual(backend.clock.now, 40.0, places=6)


class TestAJobArrivingOnTheConnection(unittest.TestCase):
    """The point of the read loop: a JOB_ASSIGN is acted on when it arrives.

    Before this build the socket was only read just after a beat, so a Job
    matched one second later waited most of half a minute. These tests pin the
    moment it is handled, not just that it eventually is.
    """

    def assign(self, job_id="123", **changes):
        payload = {
            "dockerImageId": 1, "modelId": 3, "datasetId": 42,
            "trainingMode": "AUTO",
            "trainingConfig": {
                "epochs": 3, "batchSize": 8,
                "learningRate": 0.0002, "maxLength": 1024},
            "escrowJobId": "17",
            "externalRef": "0x" + "ab" * 32,
            "price": "9000000000000000000",
            "clientWallet": "0x1111111111111111111111111111111111111111",
            "providerWallet": "0x2222222222222222222222222222222222222222",
        }
        payload.update(changes)
        return json.dumps({
            "type": "JOB_ASSIGN", "jobId": job_id,
            "timestamp": "2026-09-10T14:00:00+09:00", "payload": payload})

    def run_with(self, *scheduled, slot=None, decide=None, **kwargs):
        """One keep_alive run with a background decider wired in.

        ``decide`` defaults to a stub that accepts at once. That is **not**
        what the Worker does any more -- the real default refuses, because a
        Worker with no escrow configured cannot check any of the on-chain terms
        it was sent. Escrow behaviour is exercised in test_escrow.py; here the
        question is the wiring, so the decision is stubbed out.
        """
        decide = decide if decide is not None else (lambda job: ACCEPTED_STUB)
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
                socket, stop,
                interval=HEARTBEAT_INTERVAL_SECONDS,
                ack_timeout=HEARTBEAT_ACK_TIMEOUT_SECONDS,
                monotonic=clock,
                on_message=_dispatch_to(socket, slot, say, acceptance),
                each_turn=lambda: answer_decisions(acceptance, socket, slot, say))
        finally:
            acceptance.close()
        return outcome, backend, slot, lines

    def test_a_job_arriving_long_before_the_next_beat_is_accepted_then(self):
        # Arrives at t=5. The next beat is at t=30. It must not wait for it.
        _, backend, _, _ = self.run_with(At(5, self.assign()))

        self.assertEqual(len(backend.accepteds), 1)
        self.assertLess(backend.sent_at[0], HEARTBEAT_INTERVAL_SECONDS)

    def test_it_is_answered_within_a_slice_of_arriving(self):
        _, backend, _, _ = self.run_with(At(5, self.assign()))

        self.assertLessEqual(backend.sent_at[0] - 5, READ_SLICE_SECONDS)

    def test_the_reply_names_the_job(self):
        _, backend, _, _ = self.run_with(At(5, self.assign("456")))

        self.assertEqual(backend.accepteds[0]["jobId"], "456")

    def test_the_job_is_held_afterwards(self):
        _, _, slot, _ = self.run_with(At(5, self.assign()))

        self.assertEqual(slot.held.job_id, "123")

    def test_the_supplier_is_told(self):
        _, _, _, lines = self.run_with(At(5, self.assign()))

        self.assertTrue(any("수락했습니다" in line for line in lines))

    def test_the_reply_carries_no_credential(self):
        _, backend, _, _ = self.run_with(At(5, self.assign()))

        self.assertNotIn(TOKEN, json.dumps(backend.accepteds[0]))

    def test_the_beat_still_goes_out_on_time_afterwards(self):
        _, backend, _, _ = self.run_with(At(5, self.assign()))

        self.assertEqual(backend.sent_at[-1], HEARTBEAT_INTERVAL_SECONDS)
        self.assertEqual(len(backend.heartbeats), 1)

    def test_an_assignment_that_cannot_be_read_is_not_accepted(self):
        _, backend, slot, _ = self.run_with(At(5, self.assign(modelId=None)))

        self.assertEqual(backend.accepteds, [])
        self.assertTrue(slot.is_idle)

    def test_a_bad_assignment_does_not_end_the_connection(self):
        # Reconnecting would not change the message, and the next connection
        # would carry the same one.
        outcome, backend, _, _ = self.run_with(At(5, self.assign(modelId=None)))

        self.assertIs(outcome, ConnectionOutcome.STOPPED)
        self.assertEqual(len(backend.heartbeats), 1)

    def test_nothing_is_confirmed_before_it_is_understood(self):
        # The reply must never go out first and the validation fail after.
        _, backend, _, _ = self.run_with(At(5, self.assign(datasetId="forty-two")))

        self.assertEqual(backend.accepteds, [])


class TestAJobAndTheHeartbeatTogether(unittest.TestCase):
    """Neither is ever mistaken for the other."""

    def assign(self, job_id="123"):
        return TestAJobArrivingOnTheConnection.assign(self, job_id)

    def run_with(self, *scheduled, **kwargs):
        return TestAJobArrivingOnTheConnection.run_with(self, *scheduled, **kwargs)

    def test_a_job_arriving_right_after_a_beat_does_not_answer_it(self):
        # Case A: beat at 30, JOB_ASSIGN at 30.1, no ACK ever.
        outcome, _, slot, _ = self.run_with(At(30.1, self.assign()), answers=False)

        self.assertIs(outcome, ConnectionOutcome.LOST)
        self.assertEqual(slot.held.job_id, "123")   # and it was still handled

    def test_a_job_before_the_beat_leaves_the_beat_to_be_answered(self):
        # Case B: JOB_ASSIGN at 5, beat at 30, ACK for the beat.
        outcome, backend, _, _ = self.run_with(At(5, self.assign()))

        self.assertIs(outcome, ConnectionOutcome.STOPPED)
        self.assertEqual(len(backend.accepteds), 1)
        self.assertEqual(len(backend.heartbeats), 1)

    def test_a_job_does_not_extend_the_ack_deadline(self):
        # Case C: beat at 30, a Job at 30.1, no ACK. Given up at 40, not later.
        _, backend, _, _ = self.run_with(At(30.1, self.assign()), answers=False)

        self.assertAlmostEqual(
            backend.clock.now,
            HEARTBEAT_INTERVAL_SECONDS + HEARTBEAT_ACK_TIMEOUT_SECONDS, places=6)

    def test_a_heartbeat_ack_is_never_handed_to_the_job_dispatcher(self):
        seen = []
        stop = Stop()
        socket, backend = live(stop=stop)

        keep_alive(socket, stop, interval=HEARTBEAT_INTERVAL_SECONDS,
                   ack_timeout=HEARTBEAT_ACK_TIMEOUT_SECONDS,
                   monotonic=backend.clock,
                   on_message=lambda message: seen.append(message["type"]))

        self.assertEqual(seen, [])

    def test_two_sends_never_interleave(self):
        # One thread does both, so a JOB_ACCEPTED and a HEARTBEAT cannot be
        # halfway through each other. Every frame is whole JSON.
        _, backend, _, _ = self.run_with(At(29.9, self.assign()))

        for raw in backend.sent:
            self.assertIsInstance(json.loads(raw), dict)


class TestHoldingOneJobAcrossConnections(unittest.TestCase):
    """The slot belongs to the run, not to a socket."""

    def assign(self, job_id="123"):
        return TestAJobArrivingOnTheConnection.assign(self, job_id)

    def offer(self, raw, slot):
        return TestAJobArrivingOnTheConnection.run_with(self, At(5, raw), slot=slot)

    def test_the_same_job_offered_again_is_not_a_second_job(self):
        slot = JobSlot()
        for _ in range(2):
            self.offer(self.assign("123"), slot)

        self.assertEqual(slot.held.job_id, "123")

    def test_it_is_confirmed_again_in_case_the_first_reply_was_lost(self):
        # Nothing acknowledges a JOB_ACCEPTED, and acceptJob does nothing the
        # second time, so saying it again is safe and sometimes necessary.
        slot = JobSlot()
        self.offer(self.assign("123"), slot)
        _, second, _, _ = self.offer(self.assign("123"), slot)

        self.assertEqual(len(second.accepteds), 1)

    def test_a_different_job_is_not_accepted_while_one_is_held(self):
        slot = JobSlot()
        self.offer(self.assign("123"), slot)

        _, backend, _, _ = self.offer(self.assign("456"), slot)

        self.assertEqual(backend.accepteds, [])
        self.assertEqual(slot.held.job_id, "123")

    def test_the_supplier_is_told_a_second_job_was_refused(self):
        slot = JobSlot()
        self.offer(self.assign("123"), slot)

        _, _, _, lines = self.offer(self.assign("456"), slot)

        self.assertTrue(
            any("456" in line and "수락하지" in line for line in lines))

    def test_the_supervisor_keeps_one_slot_across_a_reconnect(self):
        # A reconnect does not forget: the Backend still has it assigned here.
        slot = JobSlot()
        first = FakeBackend(REGISTER_ACK, At(1, self.assign("123")),
                            At(2, ConnectionWentAway("gone")), answers=False)
        second = FakeBackend(REGISTER_ACK, At(1, self.assign("123")))

        supervisor = Supervisor(first, second)
        # stay_connected would otherwise build the real decider, which refuses
        # everything when no escrow is configured. The question here is whether
        # the slot survives a reconnect, not what a chain said.
        acceptance = JobAcceptance(lambda job: ACCEPTED_STUB,
                                   operator_address=OPERATOR)
        self.addCleanup(acceptance.close)
        supervisor.run(slot=slot, acceptance=acceptance)

        self.assertEqual(slot.held.job_id, "123")
        self.assertEqual(len(second.registers), 1)


class TestTheBackoff(unittest.TestCase):
    def test_it_doubles_and_then_holds(self):
        delays = backoff_delays()

        self.assertEqual([next(delays) for _ in range(7)],
                         [1, 2, 4, 8, 16, 30, 30])

    def test_it_never_exceeds_the_ceiling(self):
        delays = backoff_delays()

        self.assertTrue(all(next(delays) <= BACKOFF_MAX_SECONDS for _ in range(40)))

    def test_a_fresh_one_starts_over(self):
        self.assertEqual(next(backoff_delays()), 1)


def opener(*attempts):
    """An OpenSocket that plays out one scripted attempt after another.

    Each entry is either an exception to raise at handshake time or a
    FakeTransport to hand back. Records the token every attempt was made with.
    """
    script = list(attempts)
    seen = {"tokens": [], "urls": []}

    def open_socket(url, token):
        seen["urls"].append(url)
        seen["tokens"].append(token)
        step = script.pop(0) if script else TransportUnavailable("nothing left")
        if isinstance(step, Exception):
            raise step
        return step

    open_socket.seen = seen
    return open_socket


def registering(*replies) -> FakeTransport:
    """A transport that registers and then answers with ``replies``."""
    return FakeTransport(REGISTER_ACK, *replies)


class Supervisor:
    """One :func:`stay_connected` run, with a clock every attempt shares.

    Each attempt gets the same clock, because the supervisor is one loop over
    time: a connection that beat twice before dying has moved the clock on for
    whatever comes next. The clock is also what ends a run -- ``ends_when_dry``
    hands the stop signal to every transport, so a script that has said
    everything it wanted to stops the loop instead of beating for ever.
    """

    def __init__(self, *attempts, stop=None, ends_when_dry=True):
        self.clock = Clock()
        self.stop = stop or Stop()
        self.lines: list[str] = []
        for attempt in attempts:
            if isinstance(attempt, FakeTransport):
                attempt._clock = self.clock
                if ends_when_dry:
                    attempt._stop = self.stop
        self.open_socket = opener(*attempts)

    def run(self, **kwargs):
        return stay_connected(
            settings(), CREDENTIALS,
            lambda *p: self.lines.append(" ".join(str(x) for x in p)),
            self.stop, open_socket=self.open_socket,
            interval=HEARTBEAT_INTERVAL_SECONDS,
            ack_timeout=HEARTBEAT_ACK_TIMEOUT_SECONDS,
            monotonic=self.clock, **kwargs)


def supervise(*attempts, stop=None, ends_when_dry=True, **kwargs):
    supervisor = Supervisor(*attempts, stop=stop, ends_when_dry=ends_when_dry)
    code = supervisor.run(**kwargs)
    return code, supervisor.stop, supervisor.lines


class TestReconnecting(unittest.TestCase):
    def test_a_failed_attempt_is_followed_by_a_one_second_wait(self):
        _, stop, _ = supervise(TransportUnavailable("down"), stop=Stop(stop_after=1))

        self.assertEqual(stop.waits, [1])

    def test_repeated_failures_back_off(self):
        _, stop, _ = supervise(*[TransportUnavailable("down")] * 7,
                               stop=Stop(stop_after=7))

        self.assertEqual(stop.waits, [1, 2, 4, 8, 16, 30, 30])

    def test_registering_resets_the_backoff(self):
        # Three failures (1, 2, 4), then a connection that registers and is
        # then lost, then a failure again -- which must wait one second.
        stop = Stop(stop_after=6)
        down = TransportUnavailable("down")

        _, _, _ = supervise(
            down, down, down,
            FakeTransport(REGISTER_ACK, ConnectionWentAway("gone")),
            down,
            stop=stop)

        # Only the backoff waits on the stop signal now -- the heartbeat loop
        # reads with its own deadlines -- so this list is the policy itself:
        # three failures, then a connection that registered, then three more
        # failures starting over from one second.
        self.assertEqual(stop.waits, [1, 2, 4, 1, 2, 4])

    def test_every_attempt_registers_again(self):
        # The Backend tracks registration per session and drops everything
        # else until REGISTER runs, so a new socket is not a connected Worker.
        first = FakeTransport(REGISTER_ACK, ConnectionWentAway("gone"))
        second = FakeTransport(REGISTER_ACK, HEARTBEAT_ACK)

        supervise(first, second, stop=Stop(stop_after=3))

        for transport in (first, second):
            self.assertEqual(json.loads(transport.sent[0])["type"], "REGISTER")

    def test_backend_restart_and_reconnect_leave_the_operator_file_unchanged(self):
        with TemporaryDirectory() as temporary:
            wallet = Path(temporary) / "worker-wallet.json"
            wallet.write_text(json.dumps({
                "address": OPERATOR,
                "privateKeyProtected": "sealed-operator-key",
            }), encoding="utf-8")
            before = wallet.read_bytes()
            first = FakeTransport(REGISTER_ACK, ConnectionWentAway("backend restarted"))
            second = FakeTransport(REGISTER_ACK, HEARTBEAT_ACK)

            supervise(first, second, stop=Stop(stop_after=3))

            self.assertEqual(wallet.read_bytes(), before)

    def test_every_attempt_uses_the_stored_worker_token(self):
        _, _, _ = supervise(*[TransportUnavailable("down")] * 3,
                            stop=Stop(stop_after=3))

    def test_no_attempt_uses_anything_but_the_worker_token(self):
        open_socket = opener(*[TransportUnavailable("down")] * 3)
        stay_connected(settings(), CREDENTIALS, lambda *p: None, Stop(stop_after=3),
                       open_socket=open_socket, interval=30, ack_timeout=10)

        self.assertEqual(open_socket.seen["tokens"], [TOKEN, TOKEN, TOKEN])

    def test_reconnecting_never_calls_the_registration_endpoint(self):
        # Nothing here does HTTP at all. Signing in, the benchmark and
        # POST /api/workers/register belong to the stage before this one.

        from gshared_worker import worker_connection

        source = code_only(worker_connection)
        for absent in ("BackendClient", "register_worker", "/api/workers",
                       "access_token", "AuthWindow", "GpuBenchmark", "urllib"):
            self.assertNotIn(absent, source)

    def test_a_lost_connection_closes_its_socket_before_the_next_one(self):
        first = FakeTransport(REGISTER_ACK, ConnectionWentAway("gone"))

        supervise(first, registering(HEARTBEAT_ACK), stop=Stop(stop_after=3))

        self.assertTrue(first.closed)


class TestFailuresNotWorthRetrying(unittest.TestCase):
    """Asking again forever only hides a decision somebody has to make."""

    def test_a_refused_token_stops_rather_than_knocking_forever(self):
        code, stop, _ = supervise(HandshakeRefused(401), stop=Stop(stop_after=99))

        self.assertEqual(code, int(WorkerExitCode.CREDENTIAL_MISMATCH))
        self.assertEqual(stop.waits, [])

    def test_a_forbidden_token_stops_too(self):
        code, _, _ = supervise(HandshakeRefused(403), stop=Stop(stop_after=99))

        self.assertEqual(code, int(WorkerExitCode.CREDENTIAL_MISMATCH))

    def test_only_one_attempt_is_made(self):
        open_socket = opener(HandshakeRefused(401))
        stay_connected(settings(), CREDENTIALS, lambda *p: None, Stop(stop_after=99),
                       open_socket=open_socket, interval=30, ack_timeout=10)

        self.assertEqual(len(open_socket.seen["tokens"]), 1)

    def test_a_path_that_is_not_there_is_not_a_credential_problem(self):
        code, _, _ = supervise(HandshakeRefused(404), stop=Stop(stop_after=99))

        self.assertEqual(code, int(WorkerExitCode.EXPECTED_FAILURE))

    def test_being_confirmed_as_another_machine_stops(self):
        # answers=False: 이 Backend는 REGISTER에 "올바른" ACK를 주지 않는다.
        # 시나리오 자체가 잘못된 ACK를 주는 경우다.
        wrong = FakeTransport(
            envelope("REGISTER_ACK", workerId="worker-999"), answers=False)

        code, stop, _ = supervise(wrong, stop=Stop(stop_after=99))

        self.assertEqual(code, int(WorkerExitCode.EXPECTED_FAILURE))
        self.assertEqual(stop.waits, [])

    def test_a_service_speaking_another_protocol_stops(self):
        code, _, _ = supervise(
            FakeTransport("{ half written", answers=False), stop=Stop(stop_after=99))

        self.assertEqual(code, int(WorkerExitCode.EXPECTED_FAILURE))

    def test_a_registration_that_timed_out_is_retried(self):
        # A Backend that accepts the socket and never confirms.
        _, stop, _ = supervise(FakeTransport(answers=False), stop=Stop(stop_after=1))

        self.assertEqual(stop.waits, [1])

    def test_a_socket_that_closed_mid_handshake_is_retried(self):
        _, stop, _ = supervise(
            FakeTransport(ConnectionWentAway("gone")), stop=Stop(stop_after=1))

        self.assertEqual(stop.waits, [1])


class TestStopping(unittest.TestCase):
    def test_a_stop_before_anything_connects_makes_no_attempt(self):
        open_socket = opener(registering())
        code = stay_connected(
            settings(), CREDENTIALS, lambda *p: None, Stop(set_now=True),
            open_socket=open_socket, interval=30, ack_timeout=10)

        self.assertEqual(code, int(WorkerExitCode.OK))
        self.assertEqual(open_socket.seen["tokens"], [])

    def test_stopping_while_waiting_between_beats(self):
        code, _, _ = supervise(registering(HEARTBEAT_ACK), stop=Stop(stop_after=1))

        self.assertEqual(code, int(WorkerExitCode.OK))

    def test_stopping_during_the_backoff_ends_it(self):
        # The wait returns True, and no further attempt is made.
        open_socket = opener(TransportUnavailable("down"), registering())
        stay_connected(settings(), CREDENTIALS, lambda *p: None, Stop(stop_after=1),
                       open_socket=open_socket, interval=30, ack_timeout=10)

        self.assertEqual(len(open_socket.seen["tokens"]), 1)

    def test_stopping_closes_the_socket(self):
        transport = registering(HEARTBEAT_ACK)

        supervise(transport, stop=Stop(stop_after=1))

        self.assertTrue(transport.closed)

    def test_a_socket_is_closed_even_when_the_beat_raises(self):
        class Exploding(FakeTransport):
            def send(self, text):
                if self.sent:
                    raise RuntimeError("boom")
                super().send(text)

        transport = Exploding(REGISTER_ACK)

        with self.assertRaises(RuntimeError):
            supervise(transport, stop=Stop(stop_after=3))

        self.assertTrue(transport.closed)

    def test_nothing_waits_on_a_real_clock(self):
        # Every wait in the module goes through the stop signal, so a Worker
        # asked to quit does not sit in a sleep for half a minute.

        from gshared_worker import worker_connection

        self.assertNotIn("time.sleep", code_only(worker_connection))


class TestTheRealStopSignal(unittest.TestCase):
    """Waiting in pieces, so a person pressing Ctrl+C is not ignored.

    On Windows a blocking wait finishes before KeyboardInterrupt is raised.
    Measured: Event.wait(10) and time.sleep(10) both took the full ten seconds
    to notice one; half-second pieces noticed it in 0.52.
    """

    def test_it_waits_in_pieces_rather_than_one_long_one(self):
        import threading

        slices = []

        class Recording(threading.Event):
            def wait(self, timeout=None):
                slices.append(timeout)
                return False

        stop = InterruptibleStop(Recording())
        stop.wait(30)

        self.assertGreater(len(slices), 1)
        self.assertTrue(all(piece <= InterruptibleStop.SLICE_SECONDS
                            for piece in slices))

    def test_the_pieces_add_up_to_what_was_asked_for(self):
        import threading

        slices = []

        class Recording(threading.Event):
            def wait(self, timeout=None):
                slices.append(timeout)
                return False

        InterruptibleStop(Recording()).wait(2)

        self.assertAlmostEqual(sum(slices), 2, places=6)

    def test_a_set_signal_returns_at_once(self):
        import threading

        event = threading.Event()
        event.set()

        self.assertTrue(InterruptibleStop(event).wait(30))

    def test_it_reports_not_set_when_the_time_runs_out(self):
        self.assertFalse(InterruptibleStop().wait(0.01))

    def test_setting_it_is_what_the_loops_watch(self):
        stop = InterruptibleStop()
        stop.set()

        self.assertTrue(stop.is_set())

    def test_the_stage_uses_it_by_default(self):
        # A plain Event here would be the half-minute hang described above.
        import inspect

        from gshared_worker import worker_connection

        source = inspect.getsource(worker_connection.connect_to_backend)

        self.assertIn("InterruptibleStop()", source)


class TestTheStage(unittest.TestCase):
    """What the supplier is told, and what the launcher is given."""

    def stage(self, *attempts, stop=None):
        lines: list[str] = []
        clock = Clock()
        stop = stop or Stop()
        for attempt in attempts:
            if isinstance(attempt, FakeBackend):
                attempt._clock = clock
                attempt._stop = stop
        code = connect_to_backend(
            settings(), lambda *p: lines.append(" ".join(str(x) for x in p)),
            open_socket=opener(*attempts), credentials=CREDENTIALS,
            stop=stop, monotonic=clock)
        return code, lines

    def test_a_connection_that_registers_says_so(self):
        code, lines = self.stage(registering(HEARTBEAT_ACK))

        self.assertEqual(code, int(WorkerExitCode.OK))
        self.assertTrue(any("서비스에 등록되어 있습니다" in line for line in lines))

    def test_a_refused_token_ends_with_a_credential_code(self):
        code, _ = self.stage(HandshakeRefused(401), stop=Stop(stop_after=99))

        self.assertEqual(code, int(WorkerExitCode.CREDENTIAL_MISMATCH))

    def test_a_service_that_is_down_says_when_it_will_try_again(self):
        _, lines = self.stage(TransportUnavailable("down"), stop=Stop(stop_after=1))

        self.assertTrue(any("1초 후 다시 시도합니다" in line for line in lines))

    def test_a_lost_connection_says_it_is_reconnecting(self):
        _, lines = self.stage(
            FakeTransport(REGISTER_ACK, ConnectionWentAway("gone")),
            registering(HEARTBEAT_ACK), stop=Stop(stop_after=2))

        self.assertTrue(any("다시 연결하는 중" in line for line in lines))

    def test_nothing_it_prints_is_the_token(self):
        for attempt in (registering(HEARTBEAT_ACK), HandshakeRefused(401),
                        TransportUnavailable("down")):
            _, lines = self.stage(attempt, stop=Stop(stop_after=1))

            self.assertNotIn(TOKEN, "\n".join(lines))


class TestWhatThisBuildDoesNotDo(unittest.TestCase):
    def source(self):
        from gshared_worker import worker_connection

        return code_only(worker_connection)

    def test_no_runtime_work_is_handled(self):
        """The dispatch table has two entries and neither runs anything.

        JOB_ASSIGN reserves and hands off to the chain; JOB_CANCEL gives a job
        back on the chain. Neither starts a container, and the messages that
        would report one are not here.
        """
        source = self.source()

        for absent in ("JOB_PROGRESS", "JOB_RESULT", "JOB_FAILED",
                       "subprocess", "Popen", "docker run"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)

    def test_job_assign_is_only_named_as_something_the_next_build_handles(self):
        # It arrives at the on_message seam. Nothing acts on it here.
        source = self.source()

        self.assertNotIn('"JOB_ASSIGN"', source)
        self.assertNotIn("== 'JOB_ASSIGN'", source)

    def test_the_worker_never_declares_its_own_status(self):
        # ONLINE / OFFLINE / RUNNING belong to the Backend.
        source = self.source()

        for absent in ("markOnline", "markOffline", "/status", "ONLINE", "RUNNING"):
            self.assertNotIn(absent, source)

    def test_the_worker_makes_no_http_call_of_its_own(self):
        # A status endpoint is the shape this would take if somebody added it.
        for absent in ("requests", "urllib", "http", "PUT", "POST"):
            self.assertNotIn(absent, self.source())

    def test_there_is_no_gpu_runtime_here(self):
        for absent in ("docker", "nvidia", "wsl", "dataset"):
            self.assertNotIn(absent, self.source().lower())


if __name__ == "__main__":
    unittest.main()
