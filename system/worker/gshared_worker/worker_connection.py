"""Staying connected: heartbeats, noticing the connection has gone, coming back.

:mod:`gshared_worker.worker_socket` knows the protocol -- how to open a socket
and get REGISTER_ACK on it. This module decides what to do about it over time,
which is the same split as :mod:`gshared_worker.worker_auth` (decides a state)
and :mod:`gshared_worker.auth_check` (the stage that acts on it).

Written against the Backend's **actual** implementation, read from
``back/src/main/java/com/gshared/back/worker/websocket``:

* ``WorkerWebSocketHandler.handleHeartbeat`` calls ``touchHeartbeat`` and replies
  ``HEARTBEAT_ACK`` -- same envelope shape as REGISTER_ACK, so no ``jobId`` and
  an empty payload.
* ``WorkerHeartbeatSweeper`` marks a machine OFFLINE after **90 seconds** without
  one, sweeping every 30. Its own comment states the contract: *"HEARTBEAT 는
  30초 주기, 90초 이상 미수신이면 OFFLINE"*. Hence
  :data:`HEARTBEAT_INTERVAL_SECONDS`.
* ``handleTextMessage`` **ignores everything except REGISTER until a session has
  registered.** A heartbeat on an unregistered connection is dropped without a
  reply, so every connection registers first -- which is also why reconnecting
  is not just reopening a socket.

The Worker never says whether it is ONLINE. The Backend decides that: REGISTER
makes it online, heartbeats keep it there, a closed socket or a silent 90
seconds takes it away. There is no status call here and there must not be one.
"""

from __future__ import annotations

import logging
import threading
from enum import Enum
from typing import Any, Callable, Iterator, Protocol

from .config import WorkerSettings
from .exit_codes import WorkerExitCode
from .logging_setup import LOGGER_NAME
from .worker_credentials import WorkerCredentials, load_credentials
from .worker_socket import (
    ConnectionWentAway,
    ProtocolViolation,
    RegisteredSocket,
    SocketOutcome,
    SocketResult,
    now_in_iso8601,
    open_registered_socket,
)

_debug = logging.getLogger(LOGGER_NAME)

#: How often to tell the Backend this machine is still here.
#:
#: The Backend's sweeper gives a machine 90 seconds. Three heartbeats fit in
#: that, so a single lost one does not take a working machine offline.
HEARTBEAT_INTERVAL_SECONDS = 30

#: How long to wait for HEARTBEAT_ACK before giving up on the connection.
#:
#: Well inside the interval, so a connection that has quietly died is noticed
#: and replaced within one beat rather than sitting there looking alive. It is
#: also far inside the Backend's 90 seconds: the Worker should be reconnecting
#: long before the sweeper has to decide anything about it.
HEARTBEAT_ACK_TIMEOUT_SECONDS = 10

#: How long to wait before the first attempt after a failure, and the ceiling.
#:
#: Deterministic doubling, no jitter. Jitter spreads a thundering herd of
#: Workers reconnecting at once; there is no herd yet, and a random delay would
#: make the backoff untestable for the sake of a problem nobody has.
BACKOFF_FIRST_SECONDS = 1.0
BACKOFF_MULTIPLIER = 2
BACKOFF_MAX_SECONDS = 30.0


class StopSignal(Protocol):
    """Something that says when to stop. ``threading.Event`` satisfies it.

    A Protocol rather than a hard dependency on Event so a test can stop a loop
    at an exact point without threads, and so every wait in this module is a
    wait that can be cut short. Nothing here calls ``time.sleep``: a Worker that
    takes half a minute to notice it was asked to quit reads as one that hung.
    """

    def is_set(self) -> bool: ...

    def wait(self, timeout: float | None = None) -> bool:
        """Block until set or the timeout passes. True when it was set."""


class InterruptibleStop:
    """The real stop signal, waiting in pieces so Ctrl+C is not swallowed.

    On Windows a blocking wait runs to completion before the interpreter gets
    to raise ``KeyboardInterrupt``: measured here, ``Event.wait(10)`` and
    ``time.sleep(10)`` both took the full ten seconds to notice an interrupt,
    while waiting in half-second pieces noticed it in 0.52. With a thirty-second
    heartbeat that difference is a Worker that ignores Ctrl+C for half a minute
    and reads as hung.

    The slicing lives here rather than in the loops because it is a property of
    *this* signal on *this* platform. The loops ask for one wait of thirty
    seconds and are none the wiser, and a test drives them with its own signal
    that has no such problem.
    """

    #: Long enough not to spin, short enough that a person does not wait for it.
    SLICE_SECONDS = 0.5

    def __init__(self, event: "threading.Event | None" = None) -> None:
        self._event = event or threading.Event()

    def is_set(self) -> bool:
        return self._event.is_set()

    def set(self) -> None:
        """Ask whatever is waiting on this to stop."""
        self._event.set()

    def wait(self, timeout: float | None = None) -> bool:
        if timeout is None:
            return self._event.wait()
        remaining = timeout
        while remaining > 0:
            if self._event.wait(min(self.SLICE_SECONDS, remaining)):
                return True
            remaining -= self.SLICE_SECONDS
        return self._event.is_set()


class ConnectionOutcome(Enum):
    """Why a connection that had been registered is no longer being used."""

    #: Somebody asked the Worker to stop. Not a fault.
    STOPPED = "STOPPED"

    #: The connection failed. Opening a new one is the answer.
    LOST = "LOST"


def heartbeat_message(timestamp: str) -> dict[str, Any]:
    """The HEARTBEAT envelope, in the shape ``WorkerMessage`` deserialises.

    The payload is empty and there is no workerId in it. The Backend does not
    read one -- ``handleHeartbeat`` takes the workerId from the session, which
    the handshake resolved from the token -- so sending one would be a field
    that means nothing and could disagree with the truth.

    **No credential.** The token authenticated the handshake; it does not travel
    again on every beat.
    """
    return {"type": "HEARTBEAT", "jobId": None, "timestamp": timestamp, "payload": {}}


def backoff_delays(
    first: float = BACKOFF_FIRST_SECONDS,
    multiplier: float = BACKOFF_MULTIPLIER,
    ceiling: float = BACKOFF_MAX_SECONDS,
) -> Iterator[float]:
    """1, 2, 4, 8, 16, 30, 30, ... for ever.

    A generator so the policy is one readable line and can be tested without
    connecting to anything. A fresh one is taken after every success, which is
    what "reset the backoff" means here.
    """
    delay = first
    while True:
        yield delay
        delay = min(delay * multiplier, ceiling)


#: What to do with a well-formed message this build does not handle. The
#: default writes it down and carries on. JOB_ASSIGN will arrive here.
OnMessage = Callable[[dict[str, Any]], None]


def _note_unhandled(message: dict[str, Any]) -> None:
    """Somebody else's message. Noted, not acted on, and never taken for an ACK."""
    _debug.info("websocket: ignoring a %s message this build does not handle",
                message.get("type"))


def keep_alive(
    socket: RegisteredSocket,
    stop: StopSignal,
    *,
    interval: float = HEARTBEAT_INTERVAL_SECONDS,
    ack_timeout: float = HEARTBEAT_ACK_TIMEOUT_SECONDS,
    clock: Callable[[], str] | None = None,
    on_message: OnMessage | None = None,
    monotonic: Callable[[], float] | None = None,
) -> tuple[ConnectionOutcome, str]:
    """Beat on this connection until it fails or somebody says stop.

    Does not close the socket. Whoever opened it closes it -- see
    :func:`stay_connected` -- so there is exactly one place that does.
    """
    while True:
        if stop.wait(interval):
            return ConnectionOutcome.STOPPED, "asked to stop"

        try:
            socket.send(heartbeat_message((clock or now_in_iso8601)()))
        except ConnectionWentAway as gone:
            return ConnectionOutcome.LOST, str(gone)

        lost = _wait_for_ack(
            socket, stop, ack_timeout, on_message or _note_unhandled, monotonic)
        if lost is not None:
            return lost


def _wait_for_ack(
    socket: RegisteredSocket,
    stop: StopSignal,
    ack_timeout: float,
    on_message: OnMessage,
    monotonic: Callable[[], float] | None = None,
) -> tuple[ConnectionOutcome, str] | None:
    """Read until HEARTBEAT_ACK. None means it arrived and the beat is done.

    **The next message is not assumed to be the ACK.** The Backend can send on
    this socket whenever it likes, and reading whatever turns up as a
    confirmation would mean a JOB_ASSIGN could keep a dead connection looking
    alive. Anything that is not the ACK goes to ``on_message`` and the wait
    continues -- which is the seam the next build hangs JOB_ASSIGN on.

    The deadline is per beat rather than per read, so a chatty Backend cannot
    postpone it indefinitely one message at a time.
    """
    deadline = _Deadline(ack_timeout, monotonic)
    while True:
        remaining = deadline.remaining()
        if remaining <= 0:
            return ConnectionOutcome.LOST, "the service did not answer the heartbeat"
        try:
            message = socket.receive(remaining)
        except TimeoutError:
            return ConnectionOutcome.LOST, "the service did not answer the heartbeat"
        except ConnectionWentAway as gone:
            return ConnectionOutcome.LOST, str(gone)
        except ProtocolViolation as violation:
            # Not a message at all. The connection is no longer carrying the
            # protocol, so a new one is the answer rather than reading on.
            return ConnectionOutcome.LOST, str(violation)

        if message["type"] == "HEARTBEAT_ACK":
            return None
        if stop.is_set():
            return ConnectionOutcome.STOPPED, "asked to stop"
        on_message(message)


class _Deadline:
    """A per-heartbeat clock. Monotonic, so the wall clock changing is not a loss."""

    def __init__(self, seconds: float, monotonic: Callable[[], float] | None = None) -> None:
        import time

        self._monotonic = monotonic or time.monotonic
        self._ends_at = self._monotonic() + seconds

    def remaining(self) -> float:
        return self._ends_at - self._monotonic()


#: Failures worth opening another connection for. Everything else is a decision
#: somebody has to make, and retrying it for ever only hides it.
_WORTH_RETRYING = frozenset({
    #: The service is down, the network is out, the address is unreachable.
    SocketOutcome.UNREACHABLE,
    #: It accepted the socket and never confirmed. Could be a busy service.
    SocketOutcome.REGISTRATION_TIMED_OUT,
    #: It went away mid-handshake. A new socket is exactly the answer.
    SocketOutcome.CLOSED_BEFORE_ACK,
})


def _worth_retrying(result: SocketResult) -> bool:
    """Whether opening another connection could plausibly go differently.

    A refused handshake never qualifies. 401 and 403 are the Backend saying it
    does not accept this token, and it will say it again in a second and in an
    hour -- the answer is a person registering the machine, not a Worker
    knocking for ever. Any other handshake status is the service answering
    something this build cannot use, which a retry does not change either; the
    same reasoning already decided ``BACKEND_CONTRACT_ERROR`` in
    :mod:`gshared_worker.worker_auth`.

    ``MALFORMED_ACK`` and ``WRONG_WORKER`` are likewise not waited out. One is a
    service speaking a protocol this build does not know, the other is being
    confirmed as somebody else's machine. Neither improves by asking again.
    """
    return result.outcome in _WORTH_RETRYING


#: What each outcome means to the person reading the console, and what they
#: might do about it. Same shape as ``_IN_PLAIN_WORDS`` in
#: :mod:`gshared_worker.auth_check`.
_IN_PLAIN_WORDS = {
    SocketOutcome.HANDSHAKE_REFUSED: (
        "The G-shared service would not accept this machine's connection.",
        "Its credentials were accepted a moment ago, so this is worth reporting "
        "rather than retrying.",
    ),
    SocketOutcome.UNREACHABLE: (
        "The G-shared service could not be reached.",
        "Nothing was changed. Try again when it is back.",
    ),
    SocketOutcome.REGISTRATION_TIMED_OUT: (
        "The G-shared service accepted the connection but never confirmed this machine.",
        "Nothing was changed here. Try again in a moment.",
    ),
    SocketOutcome.MALFORMED_ACK: (
        "The G-shared service answered with something this version does not understand.",
        "This is a problem with the service rather than with this machine.",
    ),
    SocketOutcome.WRONG_WORKER: (
        "The G-shared service confirmed a different machine.",
        "This machine was not registered. Nothing was changed here.",
    ),
    SocketOutcome.CLOSED_BEFORE_ACK: (
        "The connection to the G-shared service ended before this machine was confirmed.",
        "Nothing was changed here. Try again in a moment.",
    ),
}


def stay_connected(
    settings: WorkerSettings,
    credentials: WorkerCredentials,
    say: Callable[..., None],
    stop: StopSignal,
    *,
    open_socket: Any = None,
    interval: float = HEARTBEAT_INTERVAL_SECONDS,
    ack_timeout: float = HEARTBEAT_ACK_TIMEOUT_SECONDS,
    on_message: OnMessage | None = None,
) -> int:
    """Hold a registered connection open for as long as the Worker is running.

    Open, register, beat. When that connection fails, open another one -- and
    **register on it again**, because the Backend tracks registration per
    session: ``handleRegister`` is what puts the machine in the session registry
    and marks it ONLINE, and until it runs the new socket is one the Backend
    drops every message from.

    The same stored credentials serve every attempt. Reconnecting never asks
    anybody to sign in, never touches the user's access token, never re-runs the
    benchmark and never calls the registration endpoint: none of those changed
    because a network did.

    Returns the code the launcher reads. Only a failure nobody can wait out
    ends this function on its own.
    """
    delays = backoff_delays()

    while not stop.is_set():
        result, socket = open_registered_socket(
            settings, credentials, open_socket=open_socket)

        if socket is None:
            if not _worth_retrying(result):
                return _report_giving_up(say, result)
            delay = next(delays)
            _debug.info(
                "websocket: %s -- retrying in %gs", result.outcome.value, delay)
            say(f"  Not connected ({result.detail}). Retrying in {delay:g}s...")
            if stop.wait(delay):
                break
            continue

        # Registered. Whatever it took to get here is not held against the
        # next failure, so the next one starts from one second again.
        delays = backoff_delays()
        say("  Connected. This machine is registered with the service.")
        _debug.info("websocket: registered as %s", socket.worker_id)

        try:
            outcome, detail = keep_alive(
                socket, stop, interval=interval, ack_timeout=ack_timeout,
                on_message=on_message)
        finally:
            # The one place a socket this function opened is closed.
            socket.close()

        if outcome is ConnectionOutcome.STOPPED:
            _debug.info("websocket: closed on request")
            break
        _debug.info("websocket: connection lost -- %s", detail)
        say(f"  Connection lost ({detail}). Reconnecting...")

    say("")
    say("Disconnected from the G-shared service.")
    return int(WorkerExitCode.OK)


def _report_giving_up(say: Callable[..., None], result: SocketResult) -> int:
    """Say why no further attempt will be made, and with what code."""
    _debug.info(
        "websocket: giving up -- %s%s",
        result.outcome.value, f" (HTTP {result.status})" if result.status else "",
    )
    said, remedy = _IN_PLAIN_WORDS.get(result.outcome, (result.detail, None))
    say("")
    say(said)
    if remedy:
        say(f"  {remedy}")

    if result.outcome is SocketOutcome.HANDSHAKE_REFUSED and result.status in (401, 403):
        return int(WorkerExitCode.CREDENTIAL_MISMATCH)
    return int(WorkerExitCode.EXPECTED_FAILURE)


def connect_to_backend(
    settings: WorkerSettings,
    say: Callable[..., None],
    *,
    open_socket: Any = None,
    credentials: WorkerCredentials | None = None,
    stop: StopSignal | None = None,
) -> int:
    """The stage: attach this machine to the service and stay attached.

    Reached only after :func:`gshared_worker.auth_check.authenticate_worker`
    returned OK, which is what makes reading the credentials back off disk safe
    here -- either they were already there or registration has just written
    them. Reading them here rather than carrying them down from the stage before
    keeps the token in the one place that uses it.

    **This does not return while the Worker is working.** It is the last stage,
    and a Worker that has finished connecting has not finished; it is waiting
    for the Backend to have something for it. Ctrl+C raises through the waits
    below and :func:`gshared_worker.main.run` turns that into a clean stop.
    """
    say("")
    say("Connecting to the G-shared service...")

    if credentials is None:
        loaded = load_credentials()
        if not loaded.is_present:
            # Should not happen: the stage before this one only returns OK once
            # a machine has an accepted identity. Said rather than assumed,
            # because the alternative is an AttributeError three lines down.
            _debug.info("websocket: no credentials after a successful auth stage")
            say("")
            say("G-shared cannot connect: this machine's credentials are no longer readable.")
            return int(WorkerExitCode.CREDENTIAL_MISMATCH)
        credentials = loaded.credentials

    return stay_connected(
        settings, credentials, say, stop or InterruptibleStop(),
        open_socket=open_socket)
