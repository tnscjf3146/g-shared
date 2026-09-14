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
from .escrow import AcceptOutcome, AcceptResult, JobState
from .job_cancellation import JobCancelledReady
from .job_settlement import JobResultReady, Unsettled
from .job_acceptance import Decision, JobAcceptance, Work
from .job_wiring import build_job_acceptance
from .worker_job import (
    ACCEPT_VALIDATION_FAILED,
    JOB_ASSIGN,
    JOB_CANCEL,
    USER_REQUEST,
    AssignedJob,
    JobAssignmentError,
    JobSlot,
    OfferOutcome,
    SlotState,
    job_accepted_message,
    job_cancelled_message,
    job_cancelled_terminal_message,
    job_failed_message,
    job_progress_message,
    job_rejected_message,
    job_result_message,
    read_assignment,
    read_cancellation,
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
    _debug.debug("websocket: ignoring a %s message this build does not handle",
                message.get("type"))


def take_the_job(
    message: dict[str, Any],
    socket: RegisteredSocket,
    slot: JobSlot,
    say: Callable[..., None],
    acceptance: JobAcceptance,
    *,
    clock: Callable[[], str] | None = None,
) -> None:
    """A JOB_ASSIGN: check it, reserve it, and start deciding on it.

    **This function no longer replies.** Saying yes now waits on an escrow
    acceptance that can take a minute, and the reply is sent by
    :func:`answer_decisions` when that comes back. Everything before that stays
    in order: a message is understood before it is reserved, and reserved
    before anything is signed for it.

    Returning quickly is the point. This runs on the only thread reading the
    socket and sending heartbeats.
    """
    try:
        job = read_assignment(message)
    except JobAssignmentError as refused:
        # The connection is fine and this message was well formed -- it just
        # does not describe a Job that can be run. Dropping the connection over
        # it would take a working machine offline and the next one would carry
        # the same message. Said, and carried on.
        _debug.warning("job: refused an assignment -- %s", refused)
        say(f"  작업이 들어왔지만 내용을 읽을 수 없습니다: {refused}")
        return

    outcome = slot.offer(job)
    if outcome is OfferOutcome.BUSY:
        # One GPU, one Job. Staying quiet is the answer the Backend already
        # anticipates: JobMatcher has a TODO for Jobs whose JOB_ACCEPTED never
        # arrives, and inventing a refusal message it does not read would not
        # reach anybody.
        _debug.warning(
            "job: %s offered while holding %s -- not accepted",
            job.job_id, slot.held.job_id)
        say(f"  작업 {job.job_id}: {slot.held.job_id} 작업을 처리 중이라 수락하지 않았습니다.")
        return

    if outcome is OfferOutcome.STILL_ACCEPTING:
        # The same Job, offered again while we are still deciding on it. The
        # decision under way will answer it; starting another would be a second
        # acceptJob transaction for one Job.
        _debug.debug("job: %s offered again while still deciding", job.job_id)
        return

    if outcome is OfferOutcome.ALREADY_HELD:
        # Settled already. Worth replying again -- nothing acknowledges a
        # JOB_ACCEPTED, so the first may have gone down with a connection, and
        # the Backend's acceptJob is a conditional UPDATE that does nothing the
        # second time.
        _say_yes(socket, job, AcceptResult(AcceptOutcome.ACCEPTED, "already held"),
                 acceptance.operator_address, clock)
        return

    say(f"  작업 {job.job_id}: 들어왔습니다. 수락 전에 확인하는 중...")
    if not acceptance.start(job):
        # Should not happen -- the slot was IDLE -- but a reservation with
        # nobody deciding on it would sit for ever. Given back rather than held.
        _debug.warning("job: %s could not be started, releasing", job.job_id)
        slot.release()


def cancel_the_job(
    message: dict[str, Any],
    slot: JobSlot,
    say: Callable[..., None],
    acceptance: JobAcceptance,
) -> None:
    """A JOB_CANCEL. Only one reason is acted on, and only on chain.

    ``ACCEPT_VALIDATION_FAILED`` means the Backend refused our JOB_ACCEPTED.
    At that moment the escrow job is ASSIGNED to this machine and nothing is
    going to run it, so the deposit is locked until the execution timeout
    unless somebody gives it back. That is what this does.

    **Every other reason is left alone.** A user cancellation is task 10's, and
    the difference matters because the path below ends in a transaction that
    refunds a client. Acting on a reason this build does not understand would
    be giving work away on a guess.

    Like a JOB_ASSIGN, this returns immediately: the chain work happens on the
    background thread so the heartbeat keeps its beat.
    """
    try:
        request = read_cancellation(message)
    except JobAssignmentError as refused:
        _debug.warning("job: refused a cancellation -- %s", refused)
        say(f"  취소 요청이 왔지만 내용을 읽을 수 없습니다: {refused}")
        return

    if request.reason == USER_REQUEST:
        _cancel_for_the_customer(request, slot, say, acceptance)
        return

    if not request.is_accept_validation_failure:
        # A reason this build does not know. **Not defaulted to a user
        # cancellation**: the path below ends in a transaction that refunds a
        # client, and acting on a word we do not recognise would be giving
        # work away on a guess.
        _debug.warning("job: cancellation for %s with reason %s is not handled "
                    "by this build", request.job_id, request.reason)
        say(f"  작업 {request.job_id}: 취소되었습니다 ({request.reason}). 이 버전은 해당 사유를 처리하지 않습니다.")
        return

    held = slot.held
    if held is None:
        # Nothing to give back. Reaching for the chain here would mean acting
        # on a job this machine has no record of ever holding.
        _debug.debug("job: cancellation for %s but no job is held", request.job_id)
        say(f"  작업 {request.job_id}: 취소되었지만 이 PC가 맡은 작업이 아닙니다.")
        return

    if held.job_id != request.job_id:
        _debug.warning("job: cancellation for %s while holding %s -- ignored",
                    request.job_id, held.job_id)
        say(f"  작업 {request.job_id}: 취소 요청이 왔지만 이 PC는 {held.job_id} 작업을 처리 중입니다. 아무것도 바뀌지 않았습니다.")
        return

    say(f"  작업 {request.job_id}: 서비스가 거절했습니다. 체인에서 반환하는 중...")
    if not acceptance.start_cleanup(held):
        # Busy, or no escrow configured. Either way the slot is kept: giving it
        # up while the chain may still have this job assigned here would lose
        # track of a job this machine owes.
        _debug.warning("job: cleanup for %s could not be started", request.job_id)
        say("  지금은 반환하지 못했습니다. 작업을 잃지 않도록 보관해 둡니다.")


def _cancel_for_the_customer(
    request: Any,
    slot: JobSlot,
    say: Callable[..., None],
    acceptance: JobAcceptance,
) -> None:
    """A user asked for their Job to stop. Stop it -- if it is ours.

    Returns immediately. Stopping a container, waiting for it to die and
    giving a deposit back on chain are all slow, and this is the thread that
    owes the Backend a heartbeat every thirty seconds.

    The Job's slot is **not** released here, nor when the container stops, nor
    when the transaction lands. It is released when the Backend has been told
    -- see :func:`send_terminal` -- because until then this machine is still
    the one that owes an answer for the Job.
    """
    held = slot.held
    if held is None:
        # Nothing in hand. Whatever this is about, it is not a Job this
        # machine is running, and reaching for Docker or the chain would be
        # acting on somebody else's.
        _debug.debug("cancel: %s arrived but no job is held", request.job_id)
        say(f"  작업 {request.job_id}: 취소되었지만 이 PC가 맡은 작업이 아닙니다.")
        return

    if held.job_id != request.job_id:
        _debug.warning("cancel: %s arrived while holding %s -- ignored",
                    request.job_id, held.job_id)
        say(f"  작업 {request.job_id}: 취소 요청이 왔지만 이 PC는 {held.job_id} 작업을 처리 중입니다. 아무것도 바뀌지 않았습니다.")
        return

    if not acceptance.start_cancellation(held, request.reason):
        # No runtime has started, or none is configured. The Job is kept:
        # giving the slot up while the chain may still have it assigned here
        # would lose track of something this machine owes.
        _debug.warning("cancel: %s could not be started", request.job_id)
        say(f"  작업 {request.job_id}: 취소되었지만 아직 실행 중인 것이 없습니다. 보관해 둡니다.")
        return

    say(f"  작업 {request.job_id}: 고객이 취소했습니다. 중지하는 중...")


def answer_decisions(
    acceptance: JobAcceptance,
    socket: RegisteredSocket,
    slot: JobSlot,
    say: Callable[..., None],
    *,
    clock: Callable[[], str] | None = None,
) -> None:
    """Send JOB_ACCEPTED for anything that was accepted since the last look.

    Called from the read loop, so the socket still has exactly one writer.

    **Only an acceptance sends anything.** The Backend moves a Job to RUNNING
    the moment it hears JOB_ACCEPTED, so telling it before the chain has this
    machine as the provider would put a Job in RUNNING that this machine has
    no on-chain claim to.
    """
    for decision in acceptance.take_decisions():
        _settle(decision, socket, slot, say, clock,
                acceptance.operator_address, acceptance)


def send_progress(
    acceptance: JobAcceptance,
    socket: RegisteredSocket,
    slot: JobSlot,
    clock: Callable[[], str] | None = None,
) -> bool:
    """Send the newest progress, if there is any and it is still ours.

    **Called from the connection loop and nowhere else.** The container's
    events arrive on a background thread, which puts the latest one in a box;
    this is the thread that owns the socket, taking it out. That is the whole
    arrangement, and the reason a training run cannot interleave a progress
    frame with a heartbeat.

    **At most one message per turn**, because the box holds one. A container
    that emitted three hundred steps between two turns of this loop produces
    one JOB_PROGRESS carrying the third hundredth -- so a fast workload
    cannot crowd out the heartbeat, and the Backend is told where the run is
    rather than where it was.

    Returns whether anything went out, which is for the tests; the loop does
    not care.
    """
    stopping = acceptance.terminal_for
    if stopping is not None and stopping.cancel_requested:
        # The Job is being stopped. Anything still in the box is from before
        # that and says the run is going fine.
        acceptance.progress.clear()
        return False

    progress = acceptance.progress.take()
    if progress is None:
        return False

    held = slot.held
    if held is None or held.job_id != progress.job_id:
        # A container whose Job has already been given up, or -- worse -- an
        # update for one Job sent while another is held. Neither is sent: the
        # Backend would apply it to whatever row the id named.
        _debug.debug("progress: %s is not the Job in hand; dropped",
                    progress.job_id)
        return False
    if slot.state is not SlotState.ASSIGNED:
        # Still being accepted. Nothing is training yet, and the Backend has
        # not moved the Job to RUNNING, so it would drop this anyway.
        _debug.debug("progress: %s is not confirmed yet; dropped", progress.job_id)
        return False

    try:
        socket.send(job_progress_message(progress, (clock or now_in_iso8601)()))
    except ConnectionWentAway as gone:
        # The connection is the connection's problem. A training run is hours
        # of GPU time and does not end because a frame did not go out -- the
        # loop will notice the socket and reconnect, and the snapshot is put
        # back so the reconnected one says where the run actually is.
        _debug.debug("progress: %s not sent (%s)", progress.job_id, gone)
        acceptance.progress.resend_on_next_take()
        return False
    return True


def send_terminal(
    acceptance: JobAcceptance,
    socket: RegisteredSocket,
    slot: JobSlot,
    say: Callable[..., None] | None = None,
    clock: Callable[[], str] | None = None,
) -> bool:
    """Send the Job's one terminal message, and only then let the Job go.

    **The last step of a Job, and the only place JOB_RESULT or JOB_FAILED
    leaves this Worker.** By the time an answer is waiting here the result is
    in the Backend's storage with a hash it computed itself, and the escrow
    has either paid out or refunded -- the message says so, it does not decide
    it.

    Nothing is dropped. Unlike progress, the answer stays in its channel until
    a send has actually returned; a connection that failed mid-way leaves it
    there for the next one. The Job's slot is released in the same breath as
    the successful send, so a Worker that is still trying to report is still
    holding the Job it is reporting on.
    """
    terminal = acceptance.terminal.take()
    if terminal is None:
        return False

    held = slot.held
    if held is None or held.job_id != terminal.job_id:
        # The Job was let go some other way. Dropping the answer here is
        # right: the slot is the Worker's claim on the Job, and without it
        # this is a message about somebody else's work.
        _debug.debug("terminal: %s is no longer held; dropped", terminal.job_id)
        acceptance.terminal.done(terminal)
        return False

    stamp = (clock or now_in_iso8601)()
    if isinstance(terminal, JobResultReady):
        message = job_result_message(terminal, stamp)
    elif isinstance(terminal, JobCancelledReady):
        message = job_cancelled_terminal_message(terminal, stamp)
    else:
        message = job_failed_message(terminal, stamp)
    try:
        socket.send(message)
    except ConnectionWentAway as gone:
        # Kept. The Backend has not been told, and the reconnect will try
        # again -- which is why this is a channel and not a fire-and-forget.
        _debug.warning("terminal: %s not sent (%s)", terminal.job_id, gone)
        return False

    acceptance.terminal.done(terminal)
    _say_settled(terminal, say)
    # Only now. The GPU has been free since the container stopped, but the
    # Job was not finished until the service had been told.
    slot.release()
    _debug.debug("terminal: %s reported as %s",
                terminal.job_id, message["type"])
    return True


def _say_settled(terminal: Any, say: Callable[..., None] | None) -> None:
    if say is None:
        return
    if isinstance(terminal, JobResultReady):
        say(f"  작업 {terminal.job_id}: 완료했고 결과를 보냈습니다.")
        return
    if isinstance(terminal, JobCancelledReady):
        say(f"  작업 {terminal.job_id}: 취소되어 예치금이 반환되었습니다.")
        return
    say(f"  작업 {terminal.job_id}: 종료 ({terminal.code})")


def _settle(
    decision: Decision,
    socket: RegisteredSocket,
    slot: JobSlot,
    say: Callable[..., None],
    clock: Callable[[], str] | None,
    operator_address: str = "",
    acceptance: Any = None,
) -> None:
    job, result = decision.job, decision.result
    _debug.debug("job: %s -- %s %s (%s)", job.job_id, decision.work.value,
                result.outcome.name, result.detail)

    if decision.is_cleanup:
        _settle_cleanup(decision, socket, slot, say, clock)
        return

    if decision.work is Work.RUN:
        _settle_runtime(decision, slot, say, acceptance)
        return

    if decision.work is Work.SETTLE:
        _settle_settlement(decision, slot, say)
        return

    if decision.work is Work.CANCEL:
        _settle_cancellation(decision, slot, say)
        return

    if result.accepted:
        slot.confirm()
        say(f"  작업 {job.job_id}: 수락했습니다.")
        _say_yes(socket, job, result, operator_address, clock)
        # Only now: the Backend moves the Job to RUNNING on hearing that
        # message, and the execution context is not readable before it does.
        _start_runtime(acceptance, job, say)
        return

    if result.outcome is AcceptOutcome.REFUSED:
        # A settled no. The Backend is told why -- a code it can act on --
        # and the slot is given back so the next offer can be taken.
        #
        # Releasing is safe here and only here: REFUSED means the chain gave a
        # definite answer, either before anything was signed or after a
        # transaction that demonstrably did not take the job. UNCERTAIN below
        # is the case where something of ours may still land.
        say(f"  작업 {job.job_id}: 수락하지 못했습니다: {result.detail}")
        _say_no(socket, job.job_id,
                result.code.value if result.code else "TRANSACTION_FAILED",
                result.detail, clock)
        slot.release()
        return

    # UNCERTAIN. A transaction of ours may still be in flight, and taking
    # another Job now would leave this machine owing two. The reservation is
    # kept until somebody looks -- deliberately, and said out loud. No
    # JOB_REJECTED either: saying no to a Job we may yet hold on chain would be
    # worse than saying nothing.
    say(f"  작업 {job.job_id}: 확인하지 못했습니다: {result.detail}")
    say("  아직 완료될 수 있으므로 포기하지 않고 보관해 둡니다.")


def _start_runtime(acceptance: Any, job: AssignedJob,
                   say: Callable[..., None]) -> None:
    """Hand the Job to the background pipeline, if there is one.

    A Worker with no runtime configured simply holds the Job, which is what
    every build before task 7 did. Saying so out loud matters: silence here
    would look exactly like a training run that had started.
    """
    if acceptance is None or not acceptance.start_runtime(job):
        say("  이 PC에서는 아직 작업 실행 단계가 동작하지 않습니다.")
        return
    say(f"  작업 {job.job_id}: 학습을 준비하는 중...")


def _settle_runtime(
    decision: Decision,
    slot: JobSlot,
    say: Callable[..., None],
    acceptance: Any = None,
) -> None:
    """What the training container did, and what happens about it next.

    **Still nothing is sent from here.** The container stopping is not the end
    of the Job: the result has to be stored, the escrow has to be settled, and
    only then is there something true to tell the Backend. All of that blocks,
    so it goes to the background and the answer comes back through
    :func:`send_terminal`.

    The slot is **not** released. It is this Worker's claim on the Job, and it
    is held until the service has been told what happened to it.
    """
    outcome = decision.runtime
    job = decision.job
    if outcome is None:
        return

    if outcome.succeeded:
        say(f"  작업 {job.job_id}: 학습을 마쳤습니다.")
    else:
        reason = outcome.failure.value if outcome.failure else "UNKNOWN"
        say(f"  작업 {job.job_id}: 끝내지 못했습니다: {outcome.detail} ({reason})")

    if acceptance is None or not acceptance.start_settlement(job, outcome):
        # A Worker with no settlement configured holds the Job and says so.
        # Silence here would look exactly like a result that had been sent.
        say("  이 PC에서는 아직 결과를 서비스로 보내는 단계가 동작하지 않습니다.")
        return
    say("  결과를 저장하고 에스크로를 정산하는 중...")


def _settle_cancellation(
    decision: Decision,
    slot: JobSlot,
    say: Callable[..., None],
) -> None:
    """A cancellation finished. The message itself goes out elsewhere.

    When it produced an answer, that answer is already in the terminal
    channel. What is handled here is the case where it did not: the chain and
    this Worker disagree, and neither JOB_CANCELLED nor anything else is true
    enough to send.
    """
    from .job_cancellation import ALREADY_ENDED, BACKEND_CANCEL_COMPLETION_RACE

    cancelled = decision.runtime
    job = decision.job
    if cancelled is not None and not isinstance(cancelled, Unsettled):
        return

    if getattr(cancelled, "reason", "") == ALREADY_ENDED:
        # It finished some other way first and that answer is already on its
        # way. Nothing is wrong and nothing more is sent.
        _debug.debug("cancel: %s had already ended", job.job_id)
        return

    detail = getattr(cancelled, "detail", "") or "the cancellation did not finish"
    if getattr(cancelled, "reason", "") == BACKEND_CANCEL_COMPLETION_RACE:
        # The escrow completed before the cancellation reached it. The Job is
        # finished and paid on chain; the service has it as cancelling and
        # will not accept the JOB_RESULT that is true. Neither message is
        # sent, and a person has to reconcile the two.
        say(f"  작업 {job.job_id}: 취소되는 순간 체인에서 완료되었습니다.")
        say("  서비스와 체인의 상태가 달라 보고하지 않았습니다. 보관해 둡니다.")
        return

    say(f"  작업 {job.job_id}: 깔끔하게 취소하지 못했습니다: {detail}")
    say("  보고하지 않고 보관해 둡니다.")


def _settle_settlement(
    decision: Decision,
    slot: JobSlot,
    say: Callable[..., None],
) -> None:
    """The settlement finished. The message itself goes out elsewhere.

    A settled Job's answer is already in the terminal channel by the time this
    runs; this only accounts for the case where there is no answer, which is a
    disagreement between the chain and this Worker that nobody should paper
    over by sending one of the two terminal messages anyway.
    """
    settled = decision.runtime
    job = decision.job
    if settled is not None and not isinstance(settled, Unsettled):
        return
    detail = getattr(settled, "detail", "") or "the outcome could not be settled"
    say(f"  작업 {job.job_id}: 정산하지 못했습니다: {detail}")
    say("  체인과 이 PC의 상태가 달라 보고하지 않고 보관해 둡니다.")


def _settle_cleanup(
    decision: Decision,
    socket: RegisteredSocket,
    slot: JobSlot,
    say: Callable[..., None],
    clock: Callable[[], str] | None,
) -> None:
    """What to do once the chain has answered an ``abortJob``.

    Three outcomes and three different answers, and the differences are the
    whole point:

    * **CANCELLED** -- the deposit is back with the client. Tell the Backend
      and let the slot go.
    * **UNCERTAIN** -- a transaction of ours may still land. **Keep the slot**
      and say nothing: releasing now could leave this machine holding an
      on-chain job it has forgotten about, and claiming a cancellation that
      has not happened is worse than staying quiet.
    * **REFUSED** -- the chain disagrees with the Backend. What to do next
      depends on *how*, which is why the state comes back with the result.
    """
    job, result = decision.job, decision.result

    if result.accepted:
        say(f"  작업 {job.job_id}: 체인에서 반환되었습니다. 예치금은 고객에게 돌아갑니다.")
        _say_cancelled(socket, job, result, clock)
        slot.release()
        return

    if result.outcome is AcceptOutcome.UNCERTAIN:
        say(f"  작업 {job.job_id}: 반환을 확인하지 못했습니다: {result.detail}")
        say("  트랜잭션이 아직 처리될 수 있으므로 포기하지 않고 보관해 둡니다.")
        return

    if result.state is JobState.COMPLETED:
        # The Backend says cancel; the chain says this job was finished and
        # paid. Both cannot be true. Nothing is sent and nothing is aborted --
        # a COMPLETED job has already settled, and this machine is not the
        # place to decide which side is wrong.
        _debug.warning("job: %s is COMPLETED on chain but the service cancelled it",
                    job.job_id)
        say(f"  작업 {job.job_id}: 체인에서는 이미 완료되었는데 서비스가 취소를 요청했습니다. 양쪽 모두 아무것도 바꾸지 않았습니다.")
        say("  담당자 확인이 필요합니다.")
        return

    if result.state in (JobState.FUNDED, JobState.NONE):
        # Never ours on chain, so there is nothing to give back and nothing to
        # report. The reservation is let go because holding it would block
        # real work over a job this machine never had.
        say(f"  작업 {job.job_id}: 체인에서 수락된 적이 없어 반환할 것이 없습니다.")
        slot.release()
        return

    # Assigned to a different operator, or a transaction that reverted. The
    # slot is kept: this machine cannot act on the job and cannot prove it is
    # free of it either.
    say(f"  작업 {job.job_id}: 반환하지 못했습니다: {result.detail}")
    say("  포기하지 않고 보관해 둡니다.")


def _say_cancelled(
    socket: RegisteredSocket,
    job: AssignedJob,
    result: AcceptResult,
    clock: Callable[[], str] | None,
) -> None:
    try:
        socket.send(job_cancelled_message(
            job.job_id, (clock or now_in_iso8601)(),
            reason=ACCEPT_VALIDATION_FAILED,
            abort_tx_hash=result.transaction_hash))
    except ConnectionWentAway:
        _debug.warning("job: %s released but the reply did not get out", job.job_id)


def _say_yes(
    socket: RegisteredSocket,
    job: AssignedJob,
    result: AcceptResult,
    operator_address: str,
    clock: Callable[[], str] | None,
) -> None:
    """Tell the Backend this machine took the job, and how it can check.

    Sent only after the chain has been read back and says this operator holds
    an ASSIGNED job -- see :func:`_settle`. The payload names the on-chain job,
    the key that signed and the transaction, so the Backend can verify rather
    than trust.
    """
    try:
        socket.send(job_accepted_message(
            job.job_id, (clock or now_in_iso8601)(),
            escrow_job_id=job.escrow.escrow_job_id,
            operator_address=operator_address,
            transaction_hash=result.transaction_hash))
    except ConnectionWentAway:
        # The read loop is about to find the same thing and say so. The Job
        # stays held: the Backend still has it assigned to this machine, and
        # the reply goes again if it offers it after the reconnect.
        _debug.warning("job: %s accepted but the reply did not get out", job.job_id)


def _say_no(
    socket: RegisteredSocket,
    job_id: str,
    code: str,
    detail: str,
    clock: Callable[[], str] | None,
) -> None:
    """Tell the Backend this machine will not take the job, and why.

    Worth sending even though nothing acknowledges it. Silence costs the client
    the whole accept timeout with their deposit locked, and costs the Backend
    the chance to offer the job to somebody else while it is still fundable.
    """
    try:
        socket.send(job_rejected_message(
            job_id, (clock or now_in_iso8601)(), code=code, message=detail))
    except ConnectionWentAway:
        _debug.warning("job: %s refused but the reply did not get out", job_id)


def keep_alive(
    socket: RegisteredSocket,
    stop: StopSignal,
    *,
    interval: float = HEARTBEAT_INTERVAL_SECONDS,
    ack_timeout: float = HEARTBEAT_ACK_TIMEOUT_SECONDS,
    clock: Callable[[], str] | None = None,
    on_message: OnMessage | None = None,
    monotonic: Callable[[], float] | None = None,
    each_turn: Callable[[], None] | None = None,
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

        # Whatever the background acceptance has finished deciding. Done here,
        # between reads, so the socket keeps exactly one writer -- and done
        # every turn, so a decision waits at most one read slice.
        if each_turn is not None:
            each_turn()

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
        "G-shared 서비스가 이 PC의 연결을 받아들이지 않았습니다.",
        "방금 인증은 통과했으므로 재시도보다 문의가 필요한 상황입니다.",
    ),
    SocketOutcome.UNREACHABLE: (
        "G-shared 서비스에 연결할 수 없습니다.",
        "아무것도 바뀌지 않았습니다. 서비스가 복구되면 다시 시도하세요.",
    ),
    SocketOutcome.REGISTRATION_TIMED_OUT: (
        "G-shared 서비스가 연결은 받았지만 이 PC를 확인해 주지 않았습니다.",
        "이 PC에서는 아무것도 바뀌지 않았습니다. 잠시 후 다시 시도하세요.",
    ),
    SocketOutcome.MALFORMED_ACK: (
        "G-shared 서비스가 이 버전이 이해하지 못하는 응답을 보냈습니다.",
        "이 PC가 아니라 서비스 쪽 문제입니다.",
    ),
    SocketOutcome.WRONG_WORKER: (
        "G-shared 서비스가 다른 PC를 확인했습니다.",
        "이 PC는 등록되지 않았습니다. 이 PC에서는 아무것도 바뀌지 않았습니다.",
    ),
    SocketOutcome.CLOSED_BEFORE_ACK: (
        "이 PC가 확인되기 전에 G-shared 서비스와의 연결이 끊겼습니다.",
        "이 PC에서는 아무것도 바뀌지 않았습니다. 잠시 후 다시 시도하세요.",
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
    acceptance: JobAcceptance | None = None,
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
    # One decider for the whole run, like the slot. A Job being decided on
    # does not stop being decided on because a socket closed, and a second
    # decider would be a second acceptJob for the same Job.
    owned_acceptance = acceptance is None
    # The real pipeline: escrow, runtime, settlement and cancellation, built
    # from this Worker's own settings. Without an escrow contract or a usable
    # signing key it is the refusing acceptance every earlier build had.
    acceptance = (acceptance if acceptance is not None
                  else build_job_acceptance(settings, credentials))

    try:
        return _connect_until_stopped(
            settings, credentials, say, stop, open_socket, interval, ack_timeout,
            on_message, monotonic, slot, acceptance, delays)
    finally:
        if owned_acceptance:
            acceptance.close()


def _connect_until_stopped(
    settings: WorkerSettings,
    credentials: WorkerCredentials,
    say: Callable[..., None],
    stop: StopSignal,
    open_socket: Any,
    interval: float,
    ack_timeout: float,
    on_message: OnMessage | None,
    monotonic: Callable[[], float] | None,
    slot: JobSlot,
    acceptance: JobAcceptance,
    delays: Any,
) -> int:
    while not stop.is_set():
        result, socket = open_registered_socket(
            settings, credentials, open_socket=open_socket)

        if socket is None:
            if not _worth_retrying(result):
                return _report_giving_up(say, result)
            delay = next(delays)
            _debug.warning(
                "websocket: %s -- retrying in %gs", result.outcome.value, delay)
            say(f"  연결되지 않았습니다 ({result.detail}). {delay:g}초 후 다시 시도합니다...")
            if stop.wait(delay):
                break
            continue

        # Registered. Whatever it took to get here is not held against the
        # next failure, so the next one starts from one second again.
        delays = backoff_delays()
        say("  연결되었습니다. 이 PC가 서비스에 등록되어 있습니다.")
        _debug.debug("websocket: registered as %s", socket.worker_id)
        # A training run carried on while the line was down. The Backend's
        # view of it stopped at whatever last got through, so the newest
        # snapshot is offered once on the new connection. Only the newest:
        # the steps in between were superseded while nobody was listening.
        acceptance.progress.resend_on_next_take()

        try:
            outcome, detail = keep_alive(
                socket, stop, interval=interval, ack_timeout=ack_timeout,
                on_message=on_message or _dispatch_to(
                    socket, slot, say, acceptance),
                monotonic=monotonic,
                each_turn=lambda: _every_turn(acceptance, socket, slot, say))
        finally:
            # The one place a socket this function opened is closed.
            socket.close()

        if outcome is ConnectionOutcome.STOPPED:
            _debug.debug("websocket: closed on request")
            break
        _debug.warning("websocket: connection lost -- %s", detail)
        say(f"  연결이 끊겼습니다 ({detail}). 다시 연결하는 중...")

    say("")
    say("G-shared 서비스와 연결이 끊겼습니다.")
    return int(WorkerExitCode.OK)


def _every_turn(
    acceptance: JobAcceptance,
    socket: RegisteredSocket,
    slot: JobSlot,
    say: Callable[..., None],
) -> None:
    """What the loop does between reads: answer, then report.

    Decisions first. One of them is the JOB_ACCEPTED that moves the Job to
    RUNNING at the Backend, and progress sent before it would arrive for a Job
    the Backend still has as ASSIGNED -- which it correctly ignores.

    The terminal answer last, and for the same reason in reverse: it is the
    message that ends the Job, and anything still to say about the Job should
    already have been said.
    """
    answer_decisions(acceptance, socket, slot, say)
    send_progress(acceptance, socket, slot)
    send_terminal(acceptance, socket, slot, say)


def _dispatch_to(
    socket: RegisteredSocket,
    slot: JobSlot,
    say: Callable[..., None],
    acceptance: JobAcceptance,
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
            take_the_job(message, socket, slot, say, acceptance)
            return
        if message["type"] == JOB_CANCEL:
            cancel_the_job(message, slot, say, acceptance)
            return
        _note_unhandled(message)

    return dispatch


def _report_giving_up(say: Callable[..., None], result: SocketResult) -> int:
    """Say why no further attempt will be made, and with what code."""
    _debug.warning(
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
    say("G-shared 서비스에 연결하는 중...")

    if credentials is None:
        loaded = load_credentials()
        if not loaded.is_present:
            # Should not happen: the stage before this one only returns OK once
            # a machine has an accepted identity. Said rather than assumed,
            # because the alternative is an AttributeError three lines down.
            _debug.warning("websocket: no credentials after a successful auth stage")
            say("")
            say("G-shared에 연결할 수 없습니다: 이 PC의 인증 정보를 더 이상 읽을 수 없습니다.")
            return int(WorkerExitCode.CREDENTIAL_MISMATCH)
        credentials = loaded.credentials

    return stay_connected(
        settings, credentials, say, stop or InterruptibleStop(),
        open_socket=open_socket, monotonic=monotonic)
