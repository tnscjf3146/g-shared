"""Who gets to end a Job, when three things are trying to at once.

A Job can reach its end three ways, and they can all be in flight together:

* the container finished and the result is being stored and recorded (RESULT),
* the container failed and the deposit is being returned (FAILURE),
* the customer asked for it to stop (CANCEL).

Each one ends in a transaction the chain will not let happen twice, and each
one ends in a message that tells the Backend the Job is over. Two of them
running at once is a Job that is both completed and cancelled -- on chain, in
the Backend's row, or, worst, one of each.

So there is exactly one of these objects per Job, and it holds the one fact
that settles it: **who owns the ending**.

::

    NONE ──claim──> RESULT     submitResult is about to go out
         ──claim──> FAILURE    abortJob is about to go out, run failed
         ──claim──> CANCEL     abortJob is about to go out, customer asked

The claim is taken **immediately before the chain transaction** and never
given back. Everything before that point -- packaging, uploading, even
stopping the container -- is reversible; a transaction is not.

Cancellation intent is separate from the claim, and deliberately so. Intent is
recorded the moment JOB_CANCEL arrives and is *advisory*: it makes the
pipeline stop at its next checkpoint and the settlement refuse to start. The
claim is *authority*: it decides who writes to the chain. A cancellation that
arrives after RESULT has claimed does not stop being a real cancellation -- it
simply loses, and what it does then is read the chain rather than write to it.

**The chain is the last word.** After a race, what the escrow says happened is
what happened, and this module reports that rather than what it was trying to
do.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from .escrow import AcceptOutcome, JobState, abort_escrow_job
from .job_rejection import RejectCode
from .job_runtime import RuntimeOutcome, RuntimeState
from .logging_setup import LOGGER_NAME
from .runtime_stop import RuntimeHandle, StopOutcome, stop_container
from .worker_job import USER_REQUEST

_debug = logging.getLogger(LOGGER_NAME)

#: Reported instead of a terminal message when the chain and this Worker
#: cannot be reconciled. Shares the meaning of the settlement's own constant.
RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"

#: The Job ended some other way and its owner has an answer for it.
#:
#: Carried as an ``Unsettled`` rather than as a type of its own -- everything
#: downstream sorts terminal answers from non-answers by that one check, and
#: a third kind would be put on the terminal channel and handed to a message
#: builder that does not know what it is.
ALREADY_ENDED = "ALREADY_ENDED"

#: The one case where the Backend's contract and the chain's cannot both be
#: satisfied: the escrow completed, so the Job is finished and paid, but the
#: Backend moved to CANCELLING and ``completeIfRunning`` only fires on
#: RUNNING -- it will drop the JOB_RESULT. Nothing is invented to cover the
#: gap; it is named, kept, and reported.
BACKEND_CANCEL_COMPLETION_RACE = "BACKEND_CANCEL_COMPLETION_RACE"


class TerminalClaim(Enum):
    """Who owns this Job's ending."""

    NONE = "NONE"
    RESULT = "RESULT"
    FAILURE = "FAILURE"
    CANCEL = "CANCEL"


@dataclass(frozen=True)
class JobCancelledReady:
    """A cancelled Job, ready to be reported. Built only once the chain agrees."""

    job_id: str
    reason: str
    abort_tx_hash: str | None
    actual_runtime_sec: float | None


class JobTerminal:
    """The one arbitration point for one Job. Every field behind one lock.

    A single object rather than several flags on purpose. ``is_cancelling``,
    ``is_settling`` and ``is_done`` as three booleans can disagree with each
    other, and the disagreement shows up as two transactions.
    """

    def __init__(self, job_id: str, generation: int = 0) -> None:
        self._lock = threading.Lock()
        self.job_id = job_id
        self.generation = generation
        self._claim = TerminalClaim.NONE
        self._cancel_reason: str | None = None
        self._handle: RuntimeHandle | None = None
        #: Set when a cancellation is requested. Shared with the pipeline so
        #: its phases can stop early. **Not** the Worker's shutdown signal --
        #: Ctrl+C ends the Worker, this ends one Job.
        self.token = threading.Event()

    # -- the claim ----------------------------------------------------
    @property
    def claim(self) -> TerminalClaim:
        with self._lock:
            return self._claim

    def claim_for(self, who: TerminalClaim) -> bool:
        """Take ownership of the ending, if nobody has it. Never given back.

        The compare-and-set that the whole module exists for. Called
        immediately before a chain transaction and nowhere else -- taking it
        earlier would let a cancellation lose to work that had not started.
        """
        if who is TerminalClaim.NONE:
            raise ValueError("NONE is not something to claim")
        with self._lock:
            if self._claim is TerminalClaim.NONE:
                self._claim = who
                _debug.info("terminal: %s is owned by %s", self.job_id, who.value)
                return True
            return self._claim is who

    def owned_by(self, who: TerminalClaim) -> bool:
        with self._lock:
            return self._claim is who

    @property
    def settled_elsewhere(self) -> bool:
        """Somebody other than a cancellation already owns the ending."""
        with self._lock:
            return self._claim in (TerminalClaim.RESULT, TerminalClaim.FAILURE)

    # -- the intent ---------------------------------------------------
    def request_cancel(self, reason: str) -> bool:
        """Record that the customer asked. True the first time only.

        Idempotent because the Backend may send JOB_CANCEL again after a
        reconnection, and a second cancellation workflow would be a second
        ``docker stop`` and a second ``abortJob``.
        """
        with self._lock:
            if self._cancel_reason is not None:
                _debug.info("cancel: %s was already asked to stop", self.job_id)
                return False
            self._cancel_reason = reason
        self.token.set()
        _debug.info("cancel: %s asked to stop (%s)", self.job_id, reason)
        return True

    @property
    def cancel_requested(self) -> bool:
        return self.token.is_set()

    @property
    def cancel_reason(self) -> str | None:
        with self._lock:
            return self._cancel_reason

    # -- the container ------------------------------------------------
    def container_started(self, handle: RuntimeHandle) -> None:
        with self._lock:
            self._handle = handle

    @property
    def handle(self) -> RuntimeHandle | None:
        with self._lock:
            return self._handle


def may_start_runtime(terminal: JobTerminal | None) -> bool:
    """Whether a container may still be started for this Job.

    Checked in the moment before ``docker run``, not at the top of the
    pipeline. Between deciding to run and running there is an image pull and
    a dataset download, and a cancellation that arrived during them must not
    be answered by starting the training anyway.
    """
    return terminal is None or not terminal.cancel_requested


class Cancellation:
    """Carries out one user cancellation. Returns a value; sends nothing.

    Built with the same collaborators the settlement has, and run on the same
    background thread arrangement, because it does the same kind of work:
    something slow that must not happen on the connection loop.
    """

    def __init__(
        self,
        runner: Any,
        chain: Any,
        escrow_settings: Any,
        operator_address: str,
        *,
        wait_for_runtime: Callable[[float], bool] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._runner = runner
        self._chain = chain
        self._escrow = escrow_settings
        self._operator = operator_address
        #: Blocks until the runtime thread has produced its outcome, so the
        #: chain is not touched while a container may still be training.
        self._wait_for_runtime = wait_for_runtime
        self._monotonic = monotonic or time.monotonic

    def cancel(self, job: Any, terminal: JobTerminal,
               outcome: RuntimeOutcome | None = None) -> Any:
        """Stop the Job and give the deposit back, or explain why not.

        Never raises: this runs on a background thread whose only purpose is
        to produce an answer, and an exception escaping would leave the Job
        with none and the slot held until the process ended.
        """
        try:
            return self._cancel(job, terminal, outcome)
        except Exception as error:
            _debug.exception("cancel: %s could not be cancelled", job.job_id)
            return _unsettled(
                job.job_id,
                f"the cancellation failed ({type(error).__name__})")

    def _cancel(self, job: Any, terminal: JobTerminal,
                outcome: RuntimeOutcome | None) -> Any:
        # -- 1. the container ------------------------------------------
        stopped = self._stop_runtime(terminal)
        if stopped is StopOutcome.STILL_RUNNING:
            # The one answer that must never be followed by an abort. The
            # deposit would go back to the customer while this machine went
            # on training at its own expense.
            return _unsettled(
                job.job_id,
                "the training container is still running, so the escrow was "
                "not touched")
        if stopped is StopOutcome.UNKNOWN:
            return _unsettled(
                job.job_id,
                "Docker could not say whether the container stopped, so the "
                "escrow was not touched")

        # -- 2. let the runtime thread finish --------------------------
        # It is about to notice the exit and produce a RuntimeOutcome. The
        # chain is not touched until it has, so a container still shutting
        # down cannot be aborted out from under.
        if self._wait_for_runtime is not None:
            self._wait_for_runtime(_RUNTIME_WAIT_SECONDS)

        # -- 3. who owns the ending ------------------------------------
        if not terminal.claim_for(TerminalClaim.CANCEL):
            # Lost the race. A result or a failure is already writing to the
            # chain, or has. Reading it is all that is left to do.
            _debug.info("cancel: %s is already owned by %s",
                        job.job_id, terminal.claim.value)
            return self._reconcile(job, terminal, outcome)

        # -- 4. the chain ----------------------------------------------
        given_back = abort_escrow_job(
            self._chain, self._escrow,
            escrow_job_id=job.escrow.escrow_job_id,
            operator_address=self._operator)

        if given_back.outcome is AcceptOutcome.ACCEPTED:
            return JobCancelledReady(
                job_id=job.job_id,
                reason=terminal.cancel_reason or USER_REQUEST,
                abort_tx_hash=given_back.transaction_hash,
                actual_runtime_sec=_ran_for(outcome))

        if given_back.state is JobState.COMPLETED:
            # It completed between the stop and the abort. The customer has a
            # result and the provider has been paid; saying it was cancelled
            # would be false in both directions.
            return _completed_after_cancel(job.job_id, given_back.detail)
        return _unsettled(job.job_id, given_back.detail)

    # -- the pieces ---------------------------------------------------
    def _stop_runtime(self, terminal: JobTerminal) -> StopOutcome:
        """Stop the container this run started, if it started one."""
        import asyncio

        handle = terminal.handle
        if handle is None:
            # Cancelled before ``docker run`` -- during the context fetch, the
            # model, the dataset or the pull. There is nothing to stop, and
            # ``may_start_runtime`` keeps it that way.
            _debug.info("cancel: %s had not started a container",
                        terminal.job_id)
            return StopOutcome.ALREADY_GONE
        try:
            return asyncio.run(stop_container(self._runner, handle))
        except Exception as error:
            _debug.info("cancel: stopping %s failed (%s)",
                        handle.container_name, type(error).__name__)
            return StopOutcome.UNKNOWN

    def _reconcile(self, job: Any, terminal: JobTerminal,
                   outcome: RuntimeOutcome | None) -> Any:
        """The cancellation lost. What does the chain actually say?

        Reading, never writing. Whoever holds the claim is mid-transaction or
        finished, and a second one from here is the thing this module exists
        to prevent.
        """
        try:
            on_chain = self._chain.read_job(job.escrow.escrow_job_id)
        except Exception as error:
            return _unsettled(
                job.job_id,
                f"the cancellation lost the race and the job could not be "
                f"read back ({type(error).__name__})")

        if on_chain.state is JobState.COMPLETED:
            return _completed_after_cancel(
                job.job_id, "the job completed before the cancellation")
        if on_chain.state is JobState.CANCELLED:
            # Already given back -- by the failure path, or by an earlier
            # attempt of ours. Whoever did it owns the message; this reports
            # nothing so the two cannot both be sent.
            _debug.info("cancel: %s is already cancelled, owned by %s",
                        job.job_id, terminal.claim.value)
            return _already_ended(job.job_id, terminal.claim)
        return _unsettled(
            job.job_id,
            f"the cancellation lost the race and the job is "
            f"{on_chain.state.name}")


#: How long to wait for the runtime thread to turn a stopped container into a
#: RuntimeOutcome. Bounded: a thread that never answers must not hold the
#: cancellation for ever, and the chain read below is the real check anyway.
_RUNTIME_WAIT_SECONDS = 60.0


def _completed_after_cancel(job_id: str, detail: str) -> Any:
    """The escrow completed while a cancellation was on its way.

    Reported as its own thing rather than as a failure or a cancellation. On
    chain the Job is finished and the provider is paid; in the Backend it is
    CANCELLING, and ``completeIfRunning`` only fires on RUNNING -- so the
    JOB_RESULT that is true will be dropped. The Worker cannot fix that from
    its side and must not paper over it by sending a JOB_CANCELLED that is
    not true. It is named, kept and reported.
    """
    from .job_settlement import Unsettled

    _debug.info("cancel: %s completed on chain before the cancellation", job_id)
    return Unsettled(job_id, BACKEND_CANCEL_COMPLETION_RACE, detail)


def _already_ended(job_id: str, owner: TerminalClaim) -> Any:
    """The cancellation lost and the winner has already answered.

    Not an error and not something to report: whoever owns the ending has
    either put its message on the terminal channel or already sent it, and a
    second answer for one Job is the thing this module exists to prevent.
    """
    from .job_settlement import Unsettled

    return Unsettled(job_id, ALREADY_ENDED,
                     f"the job had already ended and is owned by "
                     f"{owner.value}")


def _unsettled(job_id: str, detail: str) -> Any:
    from .job_settlement import Unsettled

    return Unsettled(job_id, RECONCILIATION_REQUIRED, detail)


def _ran_for(outcome: RuntimeOutcome | None) -> float | None:
    """How long the container ran, or nothing if it never did.

    The same definition the settlement uses, and for the same reason: the
    Scheduler trains on it. A Job cancelled before its container started has
    no runtime, and a zero here would be a measurement nobody made.
    """
    if outcome is None:
        return None
    return outcome.actual_runtime_sec
