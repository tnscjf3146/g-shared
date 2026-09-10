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
from .worker_job import (
    JOB_ASSIGN,
    AssignedJob,
    JobAssignmentError,
    JobSlot,
    OfferOutcome,
    job_accepted_message,
    read_assignment,
)
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

#: How long a single read may block for, whatever the deadlines allow.
#:
#: The same reason :class:`InterruptibleStop` waits in pieces: on Windows a
#: blocking call finishes before ``KeyboardInterrupt`` is raised, so a read that
#: could wait half a minute is half a minute of ignoring Ctrl+C. Messages do not
#: wait for the slice to end -- a read returns the moment one arrives -- so this
#: costs two buffered reads a second and nothing else.
READ_SLICE_SECONDS = 0.5

#: A read always waits a little. Zero would make the loop spin.
_SHORTEST_READ_SECONDS = 0.001

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


def take_the_job(
    message: dict[str, Any],
    socket: RegisteredSocket,
    slot: JobSlot,
    say: Callable[..., None],
    *,
    clock: Callable[[], str] | None = None,
) -> None:
    """A JOB_ASSIGN: check it, hold it, and say yes -- **in that order.**

    Nothing is confirmed before it has been understood. Sending JOB_ACCEPTED
    first and validating after would have the Backend move a Job to RUNNING on
    a machine that had already decided it could not run it.

    Nothing is started either. Accepting is agreeing to do the work; doing it
    is the GPU runtime stage and is not in this build.
    """
    try:
        job = read_assignment(message)
    except JobAssignmentError as refused:
        # The connection is fine and this message was well formed -- it just
        # does not describe a Job that can be run. Dropping the connection over
        # it would take a working machine offline and the next one would carry
        # the same message. Said, and carried on.
        _debug.info("job: refused an assignment -- %s", refused)
        say(f"  A job was offered but could not be read: {refused}")
        return

    outcome = slot.offer(job)
    if outcome is OfferOutcome.BUSY:
        # One GPU, one Job. Staying quiet is the answer the Backend already
        # anticipates: JobMatcher has a TODO for Jobs whose JOB_ACCEPTED never
        # arrives, and inventing a refusal message it does not read would not
        # reach anybody.
        _debug.info(
            "job: %s offered while holding %s -- not accepted",
            job.job_id, slot.held.job_id)
        say(f"  A second job ({job.job_id}) was offered while {slot.held.job_id}"
            " is still held. It was not accepted.")
        return

    _debug.info("job: %s %s", job.job_id, outcome.value.lower())
    try:
        socket.send(job_accepted_message(job.job_id, (clock or now_in_iso8601)()))
    except ConnectionWentAway:
        # The read loop is about to find the same thing and say so. The Job
        # stays held: the Backend still has it assigned to this machine, and
        # the reply goes again if it offers it after the reconnect.
        _debug.info("job: %s accepted but the reply did not get out", job.job_id)
        return

    if outcome is OfferOutcome.ALREADY_HELD:
        # Worth repeating. Nothing acknowledges a JOB_ACCEPTED, so the first
        # one may have gone down with a connection, and acceptJob is a
        # conditional UPDATE that does nothing the second time.
        _debug.info("job: %s was already held -- said yes again", job.job_id)
        return
    say(f"  Job {job.job_id} accepted. Waiting for the job execution stage.")


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
    """Read this connection until it fails or somebody says stop, beating on time.

    **Reading is what this loop does; the heartbeat is something it does on the
    way.** It used to be the other way round -- wait thirty seconds, beat, read
    until the ACK -- and that meant a JOB_ASSIGN arriving one second after a
    beat sat unread for most of half a minute. The Backend sends those whenever
    a Job is matched, so the loop is now shaped around always having a read
    outstanding, and the beat is a deadline the read is cut short for.

    **One reader.** Every ``receive`` on a connection happens here, so nothing
    else has to wonder whether a message it wanted went to somebody else. The
    same applies to sending: this is the only thread in the Worker, so two
    frames cannot interleave and there is no lock to get wrong.

    Does not close the socket. Whoever opened it closes it -- see
    :func:`stay_connected` -- so there is exactly one place that does.
    """
    tick = monotonic or _real_monotonic()
    handle = on_message or _note_unhandled
    beat_due_at = tick() + interval
    ack_due_at: float | None = None

    while True:
        if stop.is_set():
            return ConnectionOutcome.STOPPED, "asked to stop"

        now = tick()
        if ack_due_at is not None and now >= ack_due_at:
            # The beat went out and nothing came back in time. Messages may
            # well have arrived meanwhile -- they are not the answer to this.
            return ConnectionOutcome.LOST, "the service did not answer the heartbeat"

        if now >= beat_due_at:
            try:
                socket.send(heartbeat_message((clock or now_in_iso8601)()))
            except ConnectionWentAway as gone:
                return ConnectionOutcome.LOST, str(gone)
            beat_due_at = now + interval
            # Only the first unanswered beat starts a deadline. Resetting it on
            # every beat would let a connection that never answers live for
            # ever, one beat at a time.
            if ack_due_at is None:
                ack_due_at = now + ack_timeout
            continue

        try:
            message = socket.receive(_how_long_to_wait(now, beat_due_at, ack_due_at))
        except TimeoutError:
            # Nothing arrived in this slice. The deadlines above decide what
            # that means; here it only means go round again.
            continue
        except ConnectionWentAway as gone:
            return ConnectionOutcome.LOST, str(gone)
        except ProtocolViolation as violation:
            # Not a message at all. The connection is not carrying the protocol
            # any more, so a new one is the answer rather than reading on.
            return ConnectionOutcome.LOST, str(violation)

        if message["type"] == "HEARTBEAT_ACK":
            ack_due_at = None
            continue
        # Anything else is somebody else's. It never answers a heartbeat, no
        # matter how well formed or how recently one went out.
        handle(message)


def _how_long_to_wait(
    now: float, beat_due_at: float, ack_due_at: float | None
) -> float:
    """How long a single read may block for.

    Bounded by whichever deadline comes first, so a beat is never late because
    a read was waiting on something that had not arrived. Bounded again by
    :data:`READ_SLICE_SECONDS` so a Worker asked to stop is not held by a read
    that could have half a minute left in it.

    Never zero or less: a read that cannot wait at all turns this into a spin.
    """
    until = beat_due_at if ack_due_at is None else min(beat_due_at, ack_due_at)
    return max(min(until - now, READ_SLICE_SECONDS), _SHORTEST_READ_SECONDS)


def _real_monotonic() -> Callable[[], float]:
    import time

    return time.monotonic


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
    monotonic: Callable[[], float] | None = None,
    slot: JobSlot | None = None,
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
    # One slot for the whole run, not one per connection. A Job this Worker
    # took does not stop being its Job because a socket closed -- the Backend
    # still has it assigned here, and forgetting would mean accepting it twice
    # after the reconnect.
    slot = slot if slot is not None else JobSlot()

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
                on_message=on_message or _dispatch_to(socket, slot, say),
                monotonic=monotonic)
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


def _dispatch_to(
    socket: RegisteredSocket, slot: JobSlot, say: Callable[..., None]
) -> OnMessage:
    """What to do with a message that did not answer a heartbeat.

    The whole dispatch table, and it has one entry. A mapping rather than a
    chain of ``if``s so the next build adds a line instead of a branch --
    JOB_CANCEL and the rest arrive the same way. Anything not in it is noted
    and ignored: a Backend that learns to send something new must not be able
    to take a working Worker offline by doing so.
    """
    def dispatch(message: dict[str, Any]) -> None:
        if message["type"] == JOB_ASSIGN:
            take_the_job(message, socket, slot, say)
            return
        _note_unhandled(message)

    return dispatch


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
    monotonic: Callable[[], float] | None = None,
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
        open_socket=open_socket, monotonic=monotonic)
