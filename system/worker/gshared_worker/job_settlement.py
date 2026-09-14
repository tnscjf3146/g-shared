"""What happens after the container stops: storage, chain, and then telling.

Two flows, and the order inside each of them is the design.

**Success** ::

    FINISHED -> check the three files -> result.tar -> sha256
             -> result-upload ticket -> PUT -> result-upload/complete
             -> the Backend has hashed the stored object and agreed
             -> submitResult(jobId, 0x<sha256>)
             -> jobs() reads back COMPLETED, our operator, our hash
             -> JOB_RESULT

**Failure** ::

    FAILED   -> classify
             -> jobs() says what the chain thinks
             -> ASSIGNED and ours: abortJob   (anything else: no transaction)
             -> jobs() reads back CANCELLED
             -> JOB_FAILED

Three rules run through both.

**The chain goes last.** ``submitResult`` writes a hash that cannot be
corrected, so it is only sent once the Backend has looked at the stored object
and computed that hash itself. Doing it the other way round would leave a
number on chain with nothing behind it.

**The message goes after the chain.** JOB_RESULT means "this is finished and
paid"; sending it while the escrow still holds the deposit would separate the
Backend's idea of the Job from the money's.

**Nothing here sends anything.** This module returns a value. The WebSocket
has one owner and it is the connection loop -- see
:mod:`gshared_worker.worker_connection` -- so what crosses the thread boundary
is a :class:`JobResultReady` or a :class:`JobFailureReady` in a box that
cannot drop it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .escrow import (
    AcceptOutcome,
    JobState,
    abort_escrow_job,
    submit_escrow_result,
)
from .job_rejection import RejectCode
from .job_runtime import RuntimeFailure, RuntimeOutcome, RuntimeState
from .logging_setup import LOGGER_NAME
from .result_artifact import ARTIFACT_NAME, ArtifactRefused, pack_result
from .result_upload import UploadRefused, upload_result

_debug = logging.getLogger(LOGGER_NAME)

#: What a Job failed for when the Worker could not store its result. The
#: Backend keeps no allowlist -- ``failReason`` is "CODE: message" and it
#: takes what it is given -- so these are the Worker's own stable names, and
#: the Runtime ones come straight from :class:`RuntimeFailure`.
STORAGE_FAILURE = "STORAGE_FAILURE"
RESULT_SUBMIT_FAILED = "RESULT_SUBMIT_FAILED"

#: Not a failure code. A Job in this state is not reported as finished **or**
#: as failed: the chain and the Backend disagree in a way this Worker must not
#: paper over, and a person has to look.
RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"

#: Kept short, and kept free of anything that came out of a process. Raw
#: stderr can carry a path, a dataset fragment or a URL.
MAX_MESSAGE = 200


@dataclass(frozen=True)
class JobResultReady:
    """A finished Job, ready to be reported. Built only once everything holds."""

    job_id: str
    object_key: str
    result_hash: str
    submit_tx_hash: str | None
    actual_runtime_sec: float | None


@dataclass(frozen=True)
class JobFailureReady:
    """A Job that will not produce a result, and what the chain did about it."""

    job_id: str
    code: str
    message: str
    abort_tx_hash: str | None
    actual_runtime_sec: float | None


@dataclass(frozen=True)
class Unsettled:
    """Neither finished nor failed. Nothing is sent and the slot is kept.

    This is what a disagreement looks like. The chain holds a different
    result, or it was cancelled under a run that succeeded, or an abort could
    not be confirmed -- in every case the honest answer is that this Worker
    does not know, and inventing one of the two terminal messages would write
    it into the Backend's row as a fact.
    """

    job_id: str
    reason: str
    detail: str


class Settlement:
    """Runs one Job's terminal flow. Built with what it needs, then called.

    A class because the flow needs the same five collaborators at four
    different points, and threading them through as arguments made the
    call sites longer than the logic.
    """

    def __init__(
        self,
        client: Any,
        worker_token: str,
        chain: Any,
        escrow_settings: Any,
        operator_address: str,
        *,
        workspace_root: Path | None = None,
        sleep: Callable[[float], bool] | None = None,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self._client = client
        self._token = worker_token
        self._chain = chain
        self._escrow = escrow_settings
        self._operator = operator_address
        self._workspace_root = workspace_root
        self._sleep = sleep
        #: What performs the signed-URL PUT. ``None`` means urllib, which is
        #: what production uses; a test points it at a fake storage endpoint
        #: rather than at the network.
        self._opener = opener

    # -- the entry point ----------------------------------------------
    def settle(self, job: Any, outcome: RuntimeOutcome,
               terminal: Any = None) -> Any:
        """One Job, one answer: ready, failed, or unsettled.

        ``terminal`` is the Job's arbitration point. It is consulted twice:
        as an advisory check between phases, so a cancelled Job stops
        packaging and uploading rather than finishing work nobody wants, and
        as the **claim** immediately before a chain transaction, which is
        what actually decides who ends the Job.

        Never raises. This runs on a background thread whose only job is to
        produce a terminal answer; an exception escaping here would leave the
        Job with none and the slot held until the process ended.
        """
        try:
            if _cancelled(terminal):
                # Asked to stop before any of this began. The cancellation
                # owns the ending; settling would be a second one.
                return _stood_down(job.job_id, "before packaging")
            if outcome.state is RuntimeState.FINISHED:
                return self._settle_success(job, outcome, terminal)
            return self._settle_failure(job, outcome, terminal)
        except Exception as error:
            _debug.exception("settlement: %s could not be settled", job.job_id)
            return Unsettled(
                job.job_id, RECONCILIATION_REQUIRED,
                f"the settlement failed ({type(error).__name__})")

    # -- success ------------------------------------------------------
    def _settle_success(self, job: Any, outcome: RuntimeOutcome,
                        terminal: Any = None) -> Any:
        """Package, upload, record on chain, and only then say so."""
        try:
            packaged = pack_result(
                Path(outcome.output_dir), self._artifact_path(job, outcome))
        except ArtifactRefused as refused:
            # The container said it succeeded and did not write what it
            # promised. That is a failed Job, not a finished one.
            return self._fail_on_chain(
                job, outcome,
                RuntimeFailure.OUTPUT_VALIDATION_FAILED.value, str(refused))

        if _cancelled(terminal):
            # The archive exists on disk and nothing has left the machine.
            # Stopping here is free; stopping after the upload is not.
            return _stood_down(job.job_id, "before uploading")

        try:
            stored = upload_result(
                self._client, self._token, job.job_id, packaged.path,
                sha256=packaged.sha256, size_bytes=packaged.size_bytes,
                opener=self._opener, sleep=self._sleep)
        except UploadRefused as refused:
            # A training run nobody can collect is not a completed Job. The
            # escrow is still ASSIGNED, so the deposit is given back rather
            # than held for the execution timeout.
            _debug.warning("settlement: %s could not be stored", job.job_id)
            return self._fail_on_chain(
                job, outcome, STORAGE_FAILURE, refused.detail)

        # The final gate, and the only place the claim is taken. Everything
        # above this line is reversible -- an archive on disk, an object in
        # storage nobody has been told to pay for. Below it is a transaction
        # the chain will not let happen twice.
        #
        # A cancellation that arrives after this loses and reads the chain
        # instead; one that arrives before it wins and this stops here.
        if terminal is not None and not _claim_result(terminal):
            _debug.debug("settlement: %s is owned by the cancellation",
                        job.job_id)
            return _stood_down(job.job_id, "before submitResult")

        # Only now. The Backend has stat'd the object and computed this hash
        # from the bytes it is holding.
        recorded = submit_escrow_result(
            self._chain, self._escrow,
            escrow_job_id=job.escrow.escrow_job_id,
            operator_address=self._operator,
            result_hash=packaged.result_hash)

        if recorded.outcome is AcceptOutcome.ACCEPTED:
            return JobResultReady(
                job_id=job.job_id,
                object_key=stored.object_key,
                result_hash=packaged.result_hash,
                submit_tx_hash=recorded.transaction_hash,
                actual_runtime_sec=outcome.actual_runtime_sec)

        if recorded.code is RejectCode.RESULT_HASH_CONFLICT:
            # Two different results for one Job. Not resolvable from here and
            # not something to report either way.
            return Unsettled(job.job_id, RECONCILIATION_REQUIRED,
                             recorded.detail)
        if recorded.outcome is AcceptOutcome.UNCERTAIN:
            # A transaction that may still land. Aborting now could cancel a
            # Job that is about to complete.
            return Unsettled(job.job_id, RECONCILIATION_REQUIRED,
                             recorded.detail)

        # A settled refusal. Whether there is anything to do about it depends
        # on where the chain actually is, and only one answer involves a
        # transaction.
        if recorded.state in (None, JobState.ASSIGNED):
            # Still ours and still open -- or a transaction the contract
            # rejected outright, which returns before the job is re-read and
            # so carries no state at all. Either way the result is stored and
            # will never be paid for, so the deposit goes back rather than
            # sitting until the execution timeout. ``abort_escrow_job`` reads
            # the job itself and sends nothing unless it really is ASSIGNED
            # and ours, so the unknown case is checked rather than assumed.
            return self._fail_on_chain(
                job, outcome, RESULT_SUBMIT_FAILED, recorded.detail)

        # CANCELLED under a run that succeeded, or FUNDED again, or accepted
        # by another operator. Each of those means something happened that
        # this Worker did not do and cannot explain -- and for CANCELLED an
        # abort would "succeed" by reconciling, turning a mystery into a
        # JOB_FAILED whose cause nobody established.
        return Unsettled(job.job_id, RECONCILIATION_REQUIRED, recorded.detail)

    # -- failure ------------------------------------------------------
    def _settle_failure(self, job: Any, outcome: RuntimeOutcome,
                        terminal: Any = None) -> Any:
        failure = outcome.failure or RuntimeFailure.UNKNOWN_RUNTIME_EXIT
        return self._fail_on_chain(
            job, outcome, failure.value, outcome.detail or failure.value,
            terminal)

    def _fail_on_chain(self, job: Any, outcome: RuntimeOutcome,
                       code: str, message: str, terminal: Any = None) -> Any:
        """Give the deposit back, then report the failure.

        ``abort_escrow_job`` does the state check itself and sends nothing
        unless the job is ASSIGNED with this machine as its operator -- an
        already CANCELLED job is reconciled without a second transaction, a
        COMPLETED one is refused, and another operator's job is refused. So
        there is no blind abort here and no duplicate one.
        """
        # The failure path's own gate. Same rule, same reason: the abort is
        # a transaction, so whoever is about to send one says so first.
        if terminal is not None and not _claim_failure(terminal):
            _debug.debug("settlement: %s is owned by the cancellation",
                        job.job_id)
            return _stood_down(job.job_id, "before abortJob")

        given_back = abort_escrow_job(
            self._chain, self._escrow,
            escrow_job_id=job.escrow.escrow_job_id,
            operator_address=self._operator)

        if given_back.outcome is AcceptOutcome.ACCEPTED:
            return JobFailureReady(
                job_id=job.job_id,
                code=code,
                message=_sanitise(message),
                abort_tx_hash=given_back.transaction_hash,
                actual_runtime_sec=outcome.actual_runtime_sec)

        if given_back.state is JobState.COMPLETED:
            # A Job that failed here but is completed on chain. Somebody has
            # been paid for a result this machine did not produce, or an
            # earlier attempt of ours landed after all.
            return Unsettled(job.job_id, RECONCILIATION_REQUIRED,
                             given_back.detail)
        if given_back.outcome is AcceptOutcome.UNCERTAIN:
            return Unsettled(job.job_id, RECONCILIATION_REQUIRED,
                             given_back.detail)
        return Unsettled(job.job_id, RECONCILIATION_REQUIRED,
                         given_back.detail)

    # -- where the archive goes ---------------------------------------
    def _artifact_path(self, job: Any, outcome: RuntimeOutcome) -> Path:
        """Beside the output, not inside it.

        Inside would put ``result.tar`` in the directory the next packaging
        run walks, and the archive would start containing the previous one.
        """
        output = Path(outcome.output_dir)
        root = self._workspace_root or output.parent
        return Path(root) / ARTIFACT_NAME


def _sanitise(message: str) -> str:
    """A short, single-line reason with nothing a process wrote in it.

    The messages reaching here are the Worker's own sentences and the
    container's ``failed`` event, which is written to be shown. Raw stderr
    never reaches this function -- :func:`job_runtime._said_why` does not use
    it -- and the length cap is for the Backend's ``failReason``, which it
    truncates at 2000 characters of its own.
    """
    text = " ".join(str(message or "").split())
    if not text:
        return "the job did not finish"
    return text[:MAX_MESSAGE]


def _cancelled(terminal: Any) -> bool:
    """Whether a cancellation has been asked for. Advisory, not authority."""
    return terminal is not None and terminal.cancel_requested


def _claim_result(terminal: Any) -> bool:
    from .job_cancellation import TerminalClaim

    return terminal.claim_for(TerminalClaim.RESULT)


def _claim_failure(terminal: Any) -> bool:
    from .job_cancellation import TerminalClaim

    return terminal.claim_for(TerminalClaim.FAILURE)


#: Not a failure and not a result. The settlement stepped aside because the
#: Job is being cancelled, and the cancellation will produce the one answer.
STOOD_DOWN = "CANCELLED_BY_REQUEST"


def _stood_down(job_id: str, where: str) -> Any:
    _debug.debug("settlement: %s stood down %s", job_id, where)
    return Unsettled(job_id, STOOD_DOWN,
                     f"the job is being cancelled; the settlement stopped "
                     f"{where}")
