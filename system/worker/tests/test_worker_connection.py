"""Staying connected: beating, noticing it broke, coming back.

No sleeping and no threads. Every wait in the module under test goes through a
:class:`StopSignal`, so the fake one below decides when a wait returns and the
whole file runs in milliseconds -- a 30-second heartbeat is tested by asking
what it waited on, not by waiting.
"""

from __future__ import annotations

import json
import unittest

from gshared_worker.config import WorkerSettings
from gshared_worker.exit_codes import WorkerExitCode
from gshared_worker.worker_credentials import WorkerCredentials
from gshared_worker.worker_connection import (
    BACKOFF_MAX_SECONDS,
    HEARTBEAT_ACK_TIMEOUT_SECONDS,
    HEARTBEAT_INTERVAL_SECONDS,
    ConnectionOutcome,
    InterruptibleStop,
    backoff_delays,
    connect_to_backend,
    heartbeat_message,
    keep_alive,
    stay_connected,
)
from gshared_worker.worker_socket import (
    ConnectionWentAway,
    HandshakeRefused,
    RegisteredSocket,
    TransportUnavailable,
)

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
    return WorkerSettings(
        backend_url="wss://gpu.example.com/ws/workers", worker_id="worker-001",
        hourly_price=600, gpu_index=0, supported_workloads=(),
        worker_token=None, config_path=None)


def envelope(kind: str, **payload) -> str:
    """What the Backend sends: no jobId, empty payload unless a test wants one."""
    return json.dumps(
        {"type": kind, "timestamp": "2026-09-10T12:00:01+09:00", "payload": payload})


REGISTER_ACK = envelope("REGISTER_ACK")
HEARTBEAT_ACK = envelope("HEARTBEAT_ACK")


class Stop:
    """A stop signal a test drives by hand.

    ``waits`` records every timeout the code under test asked to wait for,
    which is how the heartbeat interval and the backoff delays are checked
    without any of them actually elapsing. ``stop_after`` sets the signal once
    that many waits have happened.
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


class FakeTransport:
    """A transport that replies with whatever the test lined up."""

    def __init__(self, *replies, on_send=None) -> None:
        self._replies = list(replies)
        self._on_send = on_send
        self.sent: list[str] = []
        self.closed = False
        self.timeouts: list[float] = []

    def send(self, text: str) -> None:
        if self._on_send is not None:
            raise self._on_send
        self.sent.append(text)

    def recv(self, timeout: float) -> str:
        self.timeouts.append(timeout)
        if not self._replies:
            raise TimeoutError
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def close(self) -> None:
        self.closed = True

    @property
    def heartbeats(self) -> list[dict]:
        return [json.loads(t) for t in self.sent if json.loads(t)["type"] == "HEARTBEAT"]


def live(*replies, on_send=None) -> tuple[RegisteredSocket, FakeTransport]:
    transport = FakeTransport(*replies, on_send=on_send)
    return RegisteredSocket(transport, "worker-001"), transport


def beat(*replies, stop=None, on_send=None, **kwargs):
    """One trip through :func:`keep_alive` with a transport that answers."""
    socket, transport = live(*replies, on_send=on_send)
    outcome, detail = keep_alive(
        socket, stop or Stop(stop_after=2), interval=30, ack_timeout=10, **kwargs)
    return outcome, detail, transport


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
    def test_it_waits_the_interval_before_the_first_beat(self):
        stop = Stop(stop_after=2)

        beat(HEARTBEAT_ACK, stop=stop)

        self.assertEqual(stop.waits[0], 30)

    def test_a_heartbeat_goes_out(self):
        _, _, transport = beat(HEARTBEAT_ACK)

        self.assertEqual(len(transport.heartbeats), 1)
        self.assertEqual(transport.heartbeats[0]["type"], "HEARTBEAT")

    def test_an_ack_keeps_the_connection(self):
        outcome, _, _ = beat(HEARTBEAT_ACK)

        self.assertIs(outcome, ConnectionOutcome.STOPPED)

    def test_several_beats_go_out_on_the_same_socket(self):
        outcome, _, transport = beat(
            HEARTBEAT_ACK, HEARTBEAT_ACK, HEARTBEAT_ACK, stop=Stop(stop_after=4))

        self.assertIs(outcome, ConnectionOutcome.STOPPED)
        self.assertEqual(len(transport.heartbeats), 3)

    def test_the_ack_wait_is_bounded(self):
        _, _, transport = beat(HEARTBEAT_ACK)

        self.assertLessEqual(transport.timeouts[0], 10)

    def test_keeping_alive_does_not_close_the_socket(self):
        # Whoever opened it closes it. One place, not two.
        _, _, transport = beat(HEARTBEAT_ACK)

        self.assertFalse(transport.closed)

    def test_no_beat_carries_a_credential(self):
        _, _, transport = beat(HEARTBEAT_ACK, HEARTBEAT_ACK, stop=Stop(stop_after=3))

        for raw in transport.sent:
            self.assertNotIn(TOKEN, raw)


class TestABeatThatIsNotAnswered(unittest.TestCase):
    def test_nothing_arriving_loses_the_connection(self):
        outcome, detail, _ = beat()  # no replies queued -> TimeoutError

        self.assertIs(outcome, ConnectionOutcome.LOST)
        self.assertIn("heartbeat", detail)

    def test_the_socket_closing_while_sending(self):
        outcome, _, _ = beat(HEARTBEAT_ACK, on_send=ConnectionWentAway("gone"))

        self.assertIs(outcome, ConnectionOutcome.LOST)

    def test_the_socket_closing_while_waiting(self):
        outcome, _, _ = beat(ConnectionWentAway("gone"))

        self.assertIs(outcome, ConnectionOutcome.LOST)

    def test_a_reply_that_is_not_json(self):
        outcome, _, _ = beat("{ half written")

        self.assertIs(outcome, ConnectionOutcome.LOST)

    def test_a_reply_with_no_type(self):
        outcome, _, _ = beat(json.dumps({"payload": {}}))

        self.assertIs(outcome, ConnectionOutcome.LOST)

    def test_a_json_value_that_is_not_a_message(self):
        outcome, _, _ = beat('"hello"')

        self.assertIs(outcome, ConnectionOutcome.LOST)


class TestOnlyAnAckCountsAsAnAck(unittest.TestCase):
    """A message arriving is not the same as the beat being answered."""

    def test_a_register_ack_arriving_mid_beat_is_not_a_heartbeat_ack(self):
        # Well formed, and not a confirmation of this beat.
        outcome, _, _ = beat(REGISTER_ACK)

        self.assertIs(outcome, ConnectionOutcome.LOST)

    def test_an_unhandled_message_does_not_end_the_beat(self):
        # It is noted and the wait continues to the real ACK.
        seen = []
        outcome, _, _ = beat(
            envelope("JOB_ASSIGN"), HEARTBEAT_ACK, on_message=seen.append)

        self.assertIs(outcome, ConnectionOutcome.STOPPED)
        self.assertEqual([m["type"] for m in seen], ["JOB_ASSIGN"])

    def test_an_unhandled_message_alone_still_loses_the_connection(self):
        # It was noted, but the beat was never answered.
        outcome, _, _ = beat(envelope("JOB_ASSIGN"), on_message=lambda m: None)

        self.assertIs(outcome, ConnectionOutcome.LOST)

    def test_the_default_handler_does_not_treat_it_as_an_ack(self):
        outcome, _, _ = beat(envelope("JOB_ASSIGN"))

        self.assertIs(outcome, ConnectionOutcome.LOST)

    def test_the_deadline_is_per_beat_not_per_read(self):
        # A chatty Backend cannot postpone the deadline one message at a time.
        # A pinned clock rather than a real one: on a fast machine three real
        # reads all round to the same tenth of a second and the test proves
        # nothing.
        ticks = iter([0, 3, 6, 9, 12])
        socket, transport = live(
            envelope("JOB_ASSIGN"), envelope("JOB_ASSIGN"), HEARTBEAT_ACK)

        keep_alive(socket, Stop(stop_after=2), interval=30, ack_timeout=10,
                   on_message=lambda m: None, monotonic=lambda: next(ticks))

        # Three reads, and each one gets what is left of the ten seconds
        # rather than ten seconds of its own.
        self.assertEqual(transport.timeouts, [7, 4, 1])


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


def supervise(*attempts, stop=None, say=None):
    lines = [] if say is None else say
    stop = stop or Stop(stop_after=1)
    code = stay_connected(
        settings(), CREDENTIALS, lambda *p: lines.append(" ".join(str(x) for x in p)),
        stop, open_socket=opener(*attempts), interval=30, ack_timeout=10)
    return code, stop, lines


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

        # waits: 1, 2, 4 (failures), 30 (the heartbeat interval), 1 (reset)
        self.assertEqual(stop.waits, [1, 2, 4, 30, 1, 2])

    def test_every_attempt_registers_again(self):
        # The Backend tracks registration per session and drops everything
        # else until REGISTER runs, so a new socket is not a connected Worker.
        first = FakeTransport(REGISTER_ACK, ConnectionWentAway("gone"))
        second = FakeTransport(REGISTER_ACK, HEARTBEAT_ACK)

        supervise(first, second, stop=Stop(stop_after=3))

        for transport in (first, second):
            self.assertEqual(json.loads(transport.sent[0])["type"], "REGISTER")

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
        import inspect

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
        wrong = FakeTransport(envelope("REGISTER_ACK", workerId="worker-999"))

        code, stop, _ = supervise(wrong, stop=Stop(stop_after=99))

        self.assertEqual(code, int(WorkerExitCode.EXPECTED_FAILURE))
        self.assertEqual(stop.waits, [])

    def test_a_service_speaking_another_protocol_stops(self):
        code, _, _ = supervise(FakeTransport("{ half written"), stop=Stop(stop_after=99))

        self.assertEqual(code, int(WorkerExitCode.EXPECTED_FAILURE))

    def test_a_service_that_is_down_is_retried_though(self):
        _, stop, _ = supervise(TransportUnavailable("down"), stop=Stop(stop_after=1))

        self.assertEqual(stop.waits, [1])

    def test_a_registration_that_timed_out_is_retried(self):
        _, stop, _ = supervise(FakeTransport(), stop=Stop(stop_after=1))

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
        import inspect

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

        event = threading.Event()
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
        code = connect_to_backend(
            settings(), lambda *p: lines.append(" ".join(str(x) for x in p)),
            open_socket=opener(*attempts), credentials=CREDENTIALS,
            stop=stop or Stop(stop_after=1))
        return code, lines

    def test_a_connection_that_registers_says_so(self):
        code, lines = self.stage(registering(HEARTBEAT_ACK))

        self.assertEqual(code, int(WorkerExitCode.OK))
        self.assertTrue(any("registered with the service" in line for line in lines))

    def test_a_refused_token_ends_with_a_credential_code(self):
        code, _ = self.stage(HandshakeRefused(401), stop=Stop(stop_after=99))

        self.assertEqual(code, int(WorkerExitCode.CREDENTIAL_MISMATCH))

    def test_a_service_that_is_down_says_when_it_will_try_again(self):
        _, lines = self.stage(TransportUnavailable("down"), stop=Stop(stop_after=1))

        self.assertTrue(any("Retrying in 1s" in line for line in lines))

    def test_a_lost_connection_says_it_is_reconnecting(self):
        _, lines = self.stage(
            FakeTransport(REGISTER_ACK, ConnectionWentAway("gone")),
            registering(HEARTBEAT_ACK), stop=Stop(stop_after=2))

        self.assertTrue(any("Reconnecting" in line for line in lines))

    def test_nothing_it_prints_is_the_token(self):
        for attempt in (registering(HEARTBEAT_ACK), HandshakeRefused(401),
                        TransportUnavailable("down")):
            _, lines = self.stage(attempt, stop=Stop(stop_after=1))

            self.assertNotIn(TOKEN, "\n".join(lines))


class TestWhatThisBuildDoesNotDo(unittest.TestCase):
    def source(self):
        from gshared_worker import worker_connection

        return code_only(worker_connection)

    def test_no_job_is_handled(self):
        for absent in ("JOB_ACCEPTED", "JOB_PROGRESS", "JOB_RESULT", "JOB_FAILED",
                       "JOB_CANCEL"):
            self.assertNotIn(absent, self.source())

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
