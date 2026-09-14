"""Deciding whether to take a Job, off the connection that offered it.

The escrow acceptance in :mod:`gshared_worker.escrow` reads a chain, signs a
transaction and waits for a receipt. That is seconds at best and can be a
minute. The WebSocket loop cannot spend that: it is the only reader on the
connection and the only thing sending heartbeats, and a Backend that hears
nothing for ninety seconds marks this machine OFFLINE. A Worker that went
offline *because* it was accepting work would be exactly backwards.

So acceptance runs on one thread of its own and answers through a queue:

* the connection loop offers a Job and goes straight back to reading
* the acceptance thread does the chain work
* the connection loop drains the answers and sends JOB_ACCEPTED itself

**The connection loop remains the only code that touches the socket.** Nothing
here receives, and nothing here sends -- which is why there is no lock: two
threads never reach the same WebSocket.

One thread, not a pool. This machine runs one Job at a time, so a second worker
would only ever be idle, and a pool would be a way to accept two Jobs at once
by accident.
"""

from __future__ import annotations

import logging
import inspect
import queue
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from .escrow import AcceptOutcome, AcceptResult
from .job_rejection import RejectCode
from .job_cancellation import JobTerminal
from .job_settlement import Unsettled
from .runtime_progress import ProgressChannel, ProgressTracker, TerminalChannel
from .logging_setup import LOGGER_NAME
from .worker_job import AssignedJob

_debug = logging.getLogger(LOGGER_NAME)

#: Decides one Job. Injected so the connection tests never touch a chain and
#: the escrow tests never touch a socket.
DecideOnJob = Callable[[AssignedJob], AcceptResult]

#: How long to wait for the acceptance thread when shutting down. Long enough
#: for a decision already in hand to be recorded, short enough that a person
#: closing the window is not left watching a receipt they no longer care about.
JOIN_TIMEOUT_SECONDS = 5


class Work(Enum):
    """What the background thread was asked to do about a Job.

    Two things now, and they end in opposite places: ACCEPT takes a job, and
    CLEANUP gives one back. They share a thread on purpose -- one job at a
    time means one piece of chain work at a time, and two threads could have
    this machine accepting and aborting the same job at once.
    """

    ACCEPT = "ACCEPT"
    CLEANUP = "CLEANUP"
    #: Prepare the inputs and run the training container. Minutes to hours.
    RUN = "RUN"
    #: Package, upload, and record the outcome on chain. Seconds to minutes,
    #: and every part of it can block -- which is why it is not done on the
    #: connection loop.
    SETTLE = "SETTLE"
    #: Stop the container and give the deposit back, because the customer
    #: asked. Blocks on Docker and on the chain, so it goes here too.
    CANCEL = "CANCEL"


@dataclass(frozen=True)
class Decision:
    """What the background thread concluded about one Job."""

    job: AssignedJob
    result: AcceptResult
    work: Work = Work.ACCEPT
    #: Only for Work.RUN. A RuntimeOutcome -- what the container did. Kept off
    #: AcceptResult because a training run is not an acceptance and squeezing
    #: it into one would make both harder to read.
    runtime: Any = None

    @property
    def accepted(self) -> bool:
        return self.result.accepted

    @property
    def is_cleanup(self) -> bool:
        return self.work is Work.CLEANUP


def refuse_without_escrow(job: AssignedJob) -> AcceptResult:
    """The default decider: refuse, because nothing has been verified.

    An earlier build accepted here. That was defensible while JOB_ASSIGN
    carried no on-chain fields and no escrow existed to check them against --
    the Worker validated the message, held the slot and replied, which is what
    it had always done.

    It is not defensible now. A JOB_ASSIGN now names an escrow job, a price and
    a client, and a Worker that answered "yes" to those without reading the
    chain would be asserting something it has not checked, to a Backend that
    moves the job to RUNNING on hearing it. Accepting unverified terms is the
    bypass this stage exists to remove, so the answer when no escrow is
    configured is no.
    """
    return AcceptResult(
        AcceptOutcome.REFUSED,
        "this Worker has no escrow configured, so the on-chain terms of the "
        "assignment could not be checked",
        code=RejectCode.ESCROW_NOT_CONFIGURED)


def escrow_cleaner(chain, settings, operator_address: str) -> DecideOnJob:
    """Gives a job back: ``abortJob`` with the same safety as ``acceptJob``.

    Used when the Backend refuses our JOB_ACCEPTED. The escrow job is ASSIGNED
    to this machine at that moment and nothing is going to run it, so leaving
    it would lock the client's deposit until the execution timeout expires --
    thirty hours on the deployed settings.
    """
    from .escrow import abort_escrow_job

    def clean_up(job: AssignedJob) -> AcceptResult:
        return abort_escrow_job(
            chain, settings, escrow_job_id=job.escrow.escrow_job_id,
            operator_address=operator_address)

    return clean_up


def escrow_decider(chain, settings, operator_address: str) -> DecideOnJob:
    """A decider that checks the chain and signs. Used when escrow is configured.

    A closure rather than a class because it holds nothing that changes: the
    chain, the settings and this machine's address are fixed for the life of a
    run.
    """
    from .escrow import accept_escrow_job

    def decide(job: AssignedJob) -> AcceptResult:
        return accept_escrow_job(
            chain, settings, job.escrow, operator_address=operator_address)

    return decide


class JobAcceptance:
    """A single background decider, and the queue it answers on.

    Started with the connection stage and stopped with it. It owns exactly one
    thread and will not start a second while the first is working -- which is
    also how a repeated JOB_ASSIGN avoids becoming a second transaction.
    """

    def __init__(self, decide: DecideOnJob | None = None,
                 operator_address: str = "",
                 clean_up: DecideOnJob | None = None,
                 run_job: Any = None,
                 settle_job: Any = None,
                 cancel_job: Any = None) -> None:
        self._decide = decide or refuse_without_escrow
        #: Gives a job back on chain. ``None`` when no escrow is configured,
        #: in which case nothing was ever accepted and there is nothing to
        #: give back -- :meth:`start_cleanup` refuses rather than pretending.
        self._clean_up = clean_up
        #: Runs the training pipeline. ``None`` when this Worker has no GPU
        #: runtime configured, in which case an accepted Job simply waits --
        #: which is what every build before task 7 did.
        self._run_job = run_job
        #: Turns a finished container into a terminal answer: package, upload,
        #: submitResult or abortJob. ``None`` when this Worker has no escrow
        #: or no Backend to settle against, in which case a finished Job is
        #: held and the supplier is told so rather than it being dropped.
        self._settle_job = settle_job
        #: Stops the container and gives the deposit back. ``None`` when this
        #: Worker has no runtime or no escrow, in which case a JOB_CANCEL is
        #: noted and the supplier told, rather than silently ignored.
        self._cancel_job = cancel_job
        #: The arbitration point for the Job in hand -- who owns its ending.
        #: One per Job, made when the runtime starts and kept until the
        #: terminal message has gone out. ``None`` between Jobs.
        self._terminal: JobTerminal | None = None
        self._runs = 0
        #: This machine's on-chain signing address. Public, and carried in
        #: JOB_ACCEPTED so the Backend can check the claim against the chain.
        #: Empty when no escrow is configured, in which case nothing is ever
        #: accepted and it is never sent.
        self.operator_address = operator_address
        self._decisions: "queue.Queue[Decision]" = queue.Queue()
        #: Where a running container's progress appears, for the connection
        #: loop to pick up. **A separate channel from the decisions queue,
        #: and deliberately so.** A decision is the answer to a Job and is
        #: never dropped; progress is a running commentary and the newest
        #: one is the only one worth having. Putting them in one queue would
        #: mean either keeping every step or risking the outcome.
        self.progress = ProgressChannel()
        #: Where a settled Job's terminal answer waits for the connection
        #: loop. **Not** the progress channel: that one coalesces and drops,
        #: and a lost JOB_RESULT is a Job the Backend holds RUNNING for ever.
        self.terminal = TerminalChannel()
        #: Whether the pipeline wants to be told where to publish progress.
        #: Decided once, by looking, so a runtime that predates this -- or a
        #: test double that returns a fixed outcome -- is called the way it
        #: expects rather than with an argument it would reject.
        self._run_job_takes_progress = _accepts(run_job, "progress")
        self._run_job_takes_terminal = _accepts(run_job, "terminal")
        self._settle_takes_terminal = _accepts_third(settle_job)
        #: What the container did, kept so a cancellation can report how long
        #: it ran. Reset when a new Job starts running.
        self._last_outcome: Any = None
        #: Set when the runtime thread has produced an outcome.
        self._runtime_done = threading.Event()
        self._cancel_thread: threading.Thread | None = None
        self._thread: threading.Thread | None = None
        self._deciding: str | None = None
        self._lock = threading.Lock()

    @property
    def busy(self) -> bool:
        """Whether a decision is in flight."""
        with self._lock:
            return self._deciding is not None

    def deciding_on(self) -> str | None:
        with self._lock:
            return self._deciding

    def start(self, job: AssignedJob) -> bool:
        """Begin deciding on this Job. False when one is already being decided.

        The refusal matters: a JOB_ASSIGN repeated while the first is still in
        flight must not start a second run, or the same Job gets two
        ``acceptJob`` transactions and the second reverts on a contract that
        has already moved past FUNDED.
        """
        with self._lock:
            if self._deciding is not None:
                _debug.debug(
                    "acceptance: already deciding on %s, ignoring %s",
                    self._deciding, job.job_id)
                return False
            self._deciding = job.job_id

        # daemon=True so a Worker whose main thread has finished is not held
        # open by a receipt nobody is waiting for. The join below is the
        # orderly path; this is the backstop.
        self._thread = threading.Thread(
            target=self._run, args=(job, Work.ACCEPT),
            name="gshared-acceptance", daemon=True)
        self._thread.start()
        return True

    def start_cleanup(self, job: AssignedJob) -> bool:
        """Give a job back on chain, in the background. False when busy.

        Same thread, same queue, same one-at-a-time rule as accepting. The
        connection loop keeps beating while this runs -- an ``abortJob`` is a
        transaction and a receipt, which is tens of seconds the heartbeat
        cannot be made to wait for.
        """
        if self._clean_up is None:
            _debug.warning("acceptance: no escrow configured, nothing to clean up")
            return False
        with self._lock:
            if self._deciding is not None:
                return False
            self._deciding = job.job_id
        self._thread = threading.Thread(
            target=self._run, args=(job, Work.CLEANUP),
            name="gshared-cleanup", daemon=True)
        self._thread.start()
        return True

    def start_runtime(self, job: AssignedJob) -> bool:
        """Prepare and run the Job in the background. False when busy.

        The same one-at-a-time rule as everything else here. One GPU means one
        training container, and a second start for a Job already running would
        put two of them on the same card -- and, worse, two of them writing
        the same output directory.
        """
        if self._run_job is None:
            _debug.warning("acceptance: no runtime configured for %s", job.job_id)
            return False
        with self._lock:
            if self._deciding is not None:
                return False
            self._deciding = job.job_id
            self._runs += 1
            self._last_outcome = None
            self._runtime_done = threading.Event()
            # One per run, and the generation goes on the container's labels
            # so a cancellation can prove which container is this one.
            self._terminal = JobTerminal(job.job_id, self._runs)
        self._thread = threading.Thread(
            target=self._run, args=(job, Work.RUN),
            name="gshared-runtime", daemon=True)
        self._thread.start()
        return True

    @property
    def terminal_for(self) -> JobTerminal | None:
        """The arbitration point for the Job in hand, if one is running."""
        with self._lock:
            return self._terminal

    def start_cancellation(self, job: AssignedJob, reason: str) -> bool:
        """Stop this Job in the background. False when it cannot be started.

        Idempotent by construction: :meth:`JobTerminal.request_cancel` is
        what records the intent, and it answers True only once, so a repeated
        JOB_CANCEL -- which the Backend sends again after a reconnection --
        does not start a second ``docker stop`` or a second ``abortJob``.
        """
        if self._cancel_job is None:
            _debug.warning("acceptance: no cancellation configured for %s",
                        job.job_id)
            return False
        with self._lock:
            terminal = self._terminal
            if terminal is None:
                # Nothing has started running. There is no container to stop
                # and no generation to name; a cancellation here would have
                # nothing to arbitrate over.
                _debug.debug("acceptance: %s has no runtime to cancel",
                            job.job_id)
                return False
            if terminal.job_id != job.job_id:
                _debug.warning("acceptance: cancellation for %s while holding %s",
                            job.job_id, terminal.job_id)
                return False

        if not terminal.request_cancel(reason):
            # Already asked. The workflow that is running owns it.
            return True

        # Whatever percentage is in the box describes a Job that is being
        # stopped. Sending it -- or replaying it after a reconnection -- would
        # show the customer their cancelled Job still making progress.
        self.progress.clear()

        # The intent is recorded whether or not a thread can start now, so
        # the pipeline and the settlement stop at their next checkpoint even
        # if the chain work has to wait for the runtime thread to finish.
        self._start_cancel_thread(job, terminal)
        return True

    def _start_cancel_thread(self, job: AssignedJob,
                             terminal: JobTerminal) -> None:
        thread = threading.Thread(
            target=self._cancel, args=(job, terminal),
            name="gshared-cancellation", daemon=True)
        self._cancel_thread = thread
        thread.start()

    def _cancel(self, job: AssignedJob, terminal: JobTerminal) -> None:
        """Run the cancellation and put its answer where the loop will find it.

        On its own thread, **not** the one-at-a-time ``_deciding`` slot: the
        runtime is very likely still occupying that, and the whole point is
        to stop it.
        """
        from .escrow import AcceptOutcome, AcceptResult

        cancelled: Any = None
        try:
            cancelled = self._cancel_job(job, terminal, self._last_outcome)
        except Exception:
            _debug.exception("acceptance: cancelling %s failed", job.job_id)
        finally:
            terminal.token.set()

        answered = cancelled is not None and not isinstance(cancelled, Unsettled)
        if answered:
            self.terminal.put(cancelled)

        result = AcceptResult(
            AcceptOutcome.ACCEPTED if answered else AcceptOutcome.REFUSED,
            _settlement_detail(cancelled))
        self._decisions.put(Decision(job, result, Work.CANCEL, cancelled))

    def start_settlement(self, job: AssignedJob, outcome: Any) -> bool:
        """Settle a finished Job in the background. False when busy or unable.

        Separate from :meth:`start_runtime` because the container has already
        stopped: the GPU is free, but the Job is not finished until its result
        is stored, recorded on chain and reported. The slot stays held for all
        of it.

        Refused while a terminal answer is still unsent. That is the guard
        against settling the same Job twice -- a second run would be a second
        ``submitResult`` or a second ``abortJob``.
        """
        if self._settle_job is None:
            _debug.warning("acceptance: no settlement configured for %s",
                        job.job_id)
            return False
        if not self.terminal.is_empty:
            _debug.debug("acceptance: %s already has a terminal answer",
                        self.terminal.job_id)
            return False
        with self._lock:
            if self._deciding is not None:
                return False
            self._deciding = job.job_id
        self._thread = threading.Thread(
            target=self._settle, args=(job, outcome),
            name="gshared-settlement", daemon=True)
        self._thread.start()
        return True

    def _settle(self, job: AssignedJob, outcome: Any) -> None:
        """Run the terminal flow and put its two answers where they belong.

        The terminal object goes into its own lossless channel; a line for the
        supplier goes on the ordinary decisions queue. They are separate on
        purpose -- the line may be missed, the answer may not.

        An :class:`~gshared_worker.job_settlement.Unsettled` is **not** put on
        the terminal channel. It is the case where the chain and this Worker
        disagree, and neither JOB_RESULT nor JOB_FAILED is true; the Job stays
        held and a person has to look.
        """
        from .escrow import AcceptOutcome, AcceptResult

        settled: Any = None
        try:
            settled = (self._settle_job(job, outcome, self.terminal_for)
                       if self._settle_takes_terminal
                       else self._settle_job(job, outcome))
        except Exception:
            _debug.exception("acceptance: settling %s failed", job.job_id)
        finally:
            with self._lock:
                self._deciding = None

        answered = settled is not None and not isinstance(settled, Unsettled)
        if answered:
            self.terminal.put(settled)

        result = AcceptResult(
            AcceptOutcome.ACCEPTED if answered else AcceptOutcome.REFUSED,
            _settlement_detail(settled))
        self._decisions.put(Decision(job, result, Work.SETTLE, settled))

    def _run(self, job: AssignedJob, work: Work = Work.ACCEPT) -> None:
        if work is Work.RUN:
            self._run_pipeline(job)
            return

        do = self._decide if work is Work.ACCEPT else self._clean_up
        try:
            result = do(job)
        except Exception as error:
            # A decider that raised told us nothing about what happened on the
            # chain, so this is not a refusal. UNCERTAIN keeps the reservation
            # rather than freeing a slot a transaction may still land against.
            _debug.exception("acceptance: deciding on %s failed", job.job_id)
            result = AcceptResult(
                AcceptOutcome.UNCERTAIN,
                f"the acceptance could not be completed ({type(error).__name__})",
                code=RejectCode.BLOCKCHAIN_RPC_FAILED)
        finally:
            with self._lock:
                self._deciding = None
        self._decisions.put(Decision(job, result, work))

    def _run_pipeline(self, job: AssignedJob) -> None:
        """Run the training pipeline and put its outcome on the same queue.

        Wrapped in its own try/finally so a pipeline that raised still frees
        the thread and still answers -- a Job with no answer would hold the
        slot until the process ended.
        """
        from .escrow import AcceptOutcome, AcceptResult
        from .job_runtime import RuntimeFailure, RuntimeOutcome, RuntimeState

        tracker = ProgressTracker(
            job.job_id, self.progress.next_generation(), self.progress)
        terminal = self.terminal_for
        try:
            outcome = (self._run_job(job, progress=tracker, terminal=terminal)
                       if self._run_job_takes_terminal
                       else self._run_job(job, progress=tracker)
                       if self._run_job_takes_progress
                       else self._run_job(job))
        except Exception as error:
            _debug.exception("acceptance: the runtime failed for %s", job.job_id)
            outcome = RuntimeOutcome(
                RuntimeState.FAILED, RuntimeFailure.DOCKER_UNAVAILABLE,
                f"the runtime failed ({type(error).__name__})")
        finally:
            # Whatever is still in the container's pipe describes a run that
            # is over. Closed before the slot is freed, so the next Job's
            # first update cannot be overtaken by this one's last.
            tracker.close()
            with self._lock:
                # Kept for the cancellation thread, which may be waiting to
                # learn how long the container ran before it was stopped.
                self._last_outcome = outcome
                self._deciding = None
            self._runtime_done.set()

        # The AcceptResult here is a carrier, not a claim about the chain: the
        # Job stays accepted whether the training succeeded or not.
        result = AcceptResult(
            AcceptOutcome.ACCEPTED if outcome.succeeded else AcceptOutcome.REFUSED,
            outcome.detail)
        self._decisions.put(Decision(job, result, Work.RUN, outcome))

    def take_decisions(self) -> list[Decision]:
        """Everything decided since the last time this was asked.

        Drained by the connection loop between reads. Never blocks: the loop
        has a heartbeat to keep and cannot wait here.
        """
        decisions = []
        while True:
            try:
                decisions.append(self._decisions.get_nowait())
            except queue.Empty:
                return decisions

    def close(self) -> None:
        """Wait briefly for the thread, then let it go.

        Called on the way out of the connection stage. A decision that arrives
        after this is dropped with the queue, which is correct: there is no
        socket left to tell the Backend about it, and the chain is the record
        that survives.
        """
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=JOIN_TIMEOUT_SECONDS)
            if thread.is_alive():
                _debug.warning("acceptance: still deciding at shutdown, leaving it")


def _accepts(what: Any, name: str) -> bool:
    """Whether a callable takes an argument by this name.

    Asked once, at construction. The alternative -- call it with the argument
    and fall back when it raises ``TypeError`` -- would also swallow a
    ``TypeError`` raised *inside* something that does accept it, and report a
    training failure as a signature mismatch.
    """
    if what is None:
        return False
    try:
        return name in inspect.signature(what).parameters
    except (TypeError, ValueError):
        # Something whose signature cannot be read. Call it the old way.
        return False


def _accepts_third(settle_job: Any) -> bool:
    """Whether the settlement takes the arbitration point as a third argument.

    Positional rather than by name, because that is how ``Settlement.settle``
    declares it and how the existing fakes in the tests are written.
    """
    if settle_job is None:
        return False
    try:
        parameters = inspect.signature(settle_job).parameters
    except (TypeError, ValueError):
        return False
    if "terminal" in parameters:
        return True
    return len([p for p in parameters.values()
                if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]) >= 3


def _settlement_detail(settled: Any) -> str:
    """One line about how a settlement ended, for the supplier's window."""
    if settled is None:
        return "the job could not be settled"
    for name in ("detail", "message", "object_key"):
        value = getattr(settled, name, None)
        if isinstance(value, str) and value:
            return value
    return "the job was settled"


def runtime_waiter(acceptance: "JobAcceptance"):
    """A callable the cancellation blocks on until the container's outcome is in.

    Bounded. A runtime thread that never answers must not hold a cancellation
    for ever -- the chain read that follows is the real check either way.
    """
    def wait(seconds: float) -> bool:
        return acceptance._runtime_done.wait(timeout=seconds)

    return wait
