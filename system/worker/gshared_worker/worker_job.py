"""Being given a Job, and saying yes to it. Not running it.

Written against the Backend's **actual** implementation, read from
``back/src/main/java/com/gshared/back``:

* ``JobMatcher.buildAssignMessage`` builds the JOB_ASSIGN payload. Every field
  below is one it puts there, with the type the ``Job`` entity declares.
* ``WorkerWebSocketHandler.handleJobAccepted`` reads the reply's **envelope**
  jobId -- not its payload -- and parses it with ``Long.parseLong``. So the
  jobId travels back exactly as it arrived and the payload stays empty.
* ``JobService.acceptJob`` is a conditional UPDATE (``startIfAssigned``). A
  second JOB_ACCEPTED for a Job already RUNNING changes nothing and is logged.
  That is what makes re-sending one safe, which matters because nothing
  acknowledges a JOB_ACCEPTED and a connection can drop right after it.

**Nothing here starts anything.** Accepting an assignment is agreeing to do the
work; doing it is the GPU runtime stage and is not in this build. What this
module leaves behind is a validated assignment sitting in memory.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .logging_setup import LOGGER_NAME

_debug = logging.getLogger(LOGGER_NAME)

JOB_ASSIGN = "JOB_ASSIGN"
JOB_ACCEPTED = "JOB_ACCEPTED"
JOB_REJECTED = "JOB_REJECTED"
JOB_CANCEL = "JOB_CANCEL"
JOB_CANCELLED = "JOB_CANCELLED"
JOB_PROGRESS = "JOB_PROGRESS"


class JobAssignmentError(ValueError):
    """A JOB_ASSIGN this Worker cannot act on.

    A **domain** problem rather than a transport one: the connection is fine and
    carrying well-formed messages, this particular one just does not describe a
    Job that can be run. Reconnecting would not change it, which is why it does
    not end the connection -- see the dispatcher in
    :mod:`gshared_worker.worker_connection`.
    """


@dataclass(frozen=True)
class TrainingSettings:
    """The four values that decide how long the work takes.

    Named apart from :class:`gshared_worker.config.WorkerSettings` on purpose --
    that one is about this machine, this one is about one Job.
    """

    epochs: int
    batch_size: int
    learning_rate: float
    max_length: int


#: An Ethereum address as text: 0x and forty hex digits.
_ADDRESS_LENGTH = 42
#: bytes32 as text: 0x and sixty-four hex digits.
_BYTES32_LENGTH = 66


@dataclass(frozen=True)
class EscrowTerms:
    """The on-chain half of an assignment. Every field is compared to the chain.

    These five arrive in the JOB_ASSIGN payload and **none of them has a
    fallback**. That is the whole design of this class. Each one could be
    guessed at -- the database job id looks like a number, a price could be
    recomputed from a rate card, the provider wallet could be assumed to be
    this machine's own -- and every one of those guesses would mean signing a
    transaction against terms nobody sent.

    The escrow job id in particular is **not** the Backend's job id. Both count
    up from one and are equal for a while during development, which is exactly
    what makes the mistake survive testing.
    """

    escrow_job_id: int
    external_ref: str
    price_wei: int
    client_wallet: str
    provider_wallet: str


def _escrow_terms(payload: dict[str, Any], job_id: str) -> EscrowTerms:
    """The five on-chain fields, or a refusal naming the one that was wrong.

    Numbers arrive as decimal **strings**. ``price`` is a uint256 in the
    token's smallest unit, and a job worth more than about nine tokens
    overflows a signed 64-bit integer -- so it travels as text and is parsed
    here with :func:`int`, which is unbounded. It is never passed through a
    float.
    """
    return EscrowTerms(
        escrow_job_id=_decimal_string(payload, "escrowJobId", job_id),
        external_ref=_hex_string(payload, "externalRef", job_id, _BYTES32_LENGTH),
        price_wei=_decimal_string(payload, "price", job_id),
        client_wallet=_hex_string(payload, "clientWallet", job_id, _ADDRESS_LENGTH),
        provider_wallet=_hex_string(payload, "providerWallet", job_id, _ADDRESS_LENGTH),
    )


def _decimal_string(values: dict[str, Any], name: str, job_id: str) -> int:
    """An unsigned integer sent as a JSON string.

    A JSON number is refused rather than accepted and converted. A uint256 that
    went through a JavaScript number on the way here has already lost digits,
    and the value would look perfectly reasonable -- so the type itself is the
    signal that it did not.
    """
    value = values.get(name)
    if value is None:
        raise JobAssignmentError(f"assignment {job_id}: {name} is missing")
    if not isinstance(value, str):
        raise JobAssignmentError(
            f"assignment {job_id}: {name} must be a decimal string, not a "
            f"JSON number -- it is a uint256")
    text = value.strip()
    if not text.isdigit():
        raise JobAssignmentError(
            f"assignment {job_id}: {name} is not an unsigned decimal integer")
    return int(text)


def _hex_string(values: dict[str, Any], name: str, job_id: str, length: int) -> str:
    """``0x`` and exactly the right number of hex digits."""
    value = values.get(name)
    if value is None:
        raise JobAssignmentError(f"assignment {job_id}: {name} is missing")
    if not isinstance(value, str):
        raise JobAssignmentError(f"assignment {job_id}: {name} is not a string")
    text = value.strip()
    if len(text) != length or not text.startswith("0x"):
        raise JobAssignmentError(
            f"assignment {job_id}: {name} is not {length - 2} hex digits "
            f"behind 0x")
    if any(character not in "0123456789abcdefABCDEF" for character in text[2:]):
        raise JobAssignmentError(
            f"assignment {job_id}: {name} has characters that are not hex")
    return text


@dataclass(frozen=True)
class AssignedJob:
    """One Job this Worker has been given, with everything the Backend sent.

    ``job_id`` stays a **string**, exactly as it arrived. ``JobMatcher`` sends
    ``String.valueOf(job.getId())`` and ``handleJobAccepted`` parses what comes
    back with ``Long.parseLong``, so a round trip through a Python int would be
    a conversion in both directions for no gain and one more place to differ.

    The other three ids are integers because the Java fields are ``Long`` and
    the payload carries them as JSON numbers -- since V8 the catalogue keys are
    integers, and they are the same values the Scheduler was asked about.
    """

    job_id: str
    docker_image_id: int
    model_id: int
    dataset_id: int
    training_mode: str
    training: TrainingSettings
    #: The on-chain terms. Required -- a JOB_ASSIGN without them is refused
    #: before anything is reserved, because escrow acceptance cannot start
    #: without them and inventing them is the one thing that must never happen.
    escrow: EscrowTerms


class SlotState(Enum):
    """What the one slot is doing.

    ``ACCEPTING`` exists because taking a Job stopped being instant. Between
    the assignment arriving and this machine being the provider on chain there
    is a transaction, and during it the slot is neither free nor settled: a
    second Job must not be taken, and the first is not yet accepted.
    """

    IDLE = "IDLE"
    #: Reserved while the escrow acceptance runs. Not yet confirmed.
    ACCEPTING = "ACCEPTING"
    #: Accepted -- on chain when an escrow is configured, locally when not.
    ASSIGNED = "ASSIGNED"


class OfferOutcome(Enum):
    """What happened when a Job was offered to this Worker."""

    #: It was idle and now holds this Job.
    ACCEPTED = "ACCEPTED"

    #: It already holds this same Job. Nothing changed and nothing is wrong --
    #: the Backend may resend after a reconnect, and the reply is worth
    #: repeating in case the first one was lost with the old connection.
    ALREADY_HELD = "ALREADY_HELD"

    #: This same Job is still being accepted. Not an error and not a second
    #: offer to act on -- the acceptance already under way will answer it.
    STILL_ACCEPTING = "STILL_ACCEPTING"

    #: It is holding a different Job. One GPU does one Job at a time.
    BUSY = "BUSY"


class JobSlot:
    """The one Job this Worker can hold. Empty, or holding exactly one.

    A class rather than a variable because "is this the one we already have"
    and "are we free" are questions with answers, and the answers decide what
    goes back on the wire.

    **In memory only.** A Worker that is restarted has forgotten, and that is
    deliberate: writing an assignment to disk would mean a Worker could come
    back believing it owes work the Backend has long since given to somebody
    else. Surviving a restart is a problem for whoever adds the runtime, and it
    needs the Backend's view to solve rather than a local file.
    """

    def __init__(self) -> None:
        self._held: AssignedJob | None = None
        self._state = SlotState.IDLE

    @property
    def held(self) -> AssignedJob | None:
        return self._held

    @property
    def state(self) -> SlotState:
        return self._state

    @property
    def is_idle(self) -> bool:
        return self._state is SlotState.IDLE

    @property
    def is_accepting(self) -> bool:
        return self._state is SlotState.ACCEPTING

    def offer(self, job: AssignedJob) -> OfferOutcome:
        """Take this Job if the slot is free, and say what happened.

        A Job taken here is **reserved, not accepted**: the slot moves to
        ACCEPTING and stays there until :meth:`confirm` or :meth:`release`.
        Whoever offers it is responsible for reaching one of those.
        """
        if self._state is SlotState.IDLE:
            self._held = job
            self._state = SlotState.ACCEPTING
            return OfferOutcome.ACCEPTED
        if self._held.job_id != job.job_id:
            return OfferOutcome.BUSY
        if self._state is SlotState.ACCEPTING:
            return OfferOutcome.STILL_ACCEPTING
        return OfferOutcome.ALREADY_HELD

    def confirm(self) -> None:
        """The reservation held. This machine is committed to the Job."""
        if self._state is SlotState.ACCEPTING:
            self._state = SlotState.ASSIGNED

    def release(self) -> None:
        """Give the slot back.

        Called when an acceptance settled as a no. **Not** called when the
        outcome is unknown: a slot given up while a transaction of ours may
        still land is a machine that owes two Jobs.
        """
        self._held = None
        self._state = SlotState.IDLE


def read_assignment(message: dict[str, Any]) -> AssignedJob:
    """A JOB_ASSIGN envelope as something with fields, or a refusal.

    Every field is checked before any of it is believed. The alternative is a
    ``KeyError`` three stages later, in the runtime, with a container half
    built -- and by then the message that caused it is long gone.
    """
    job_id = message.get("jobId")
    if not isinstance(job_id, str) or not job_id.strip():
        # The envelope's jobId, not the payload's. It is what has to travel
        # back, and handleJobAccepted parses it with Long.parseLong.
        raise JobAssignmentError("the assignment did not say which Job it is")

    payload = message.get("payload")
    if not isinstance(payload, dict):
        raise JobAssignmentError(f"assignment {job_id} carried no details")

    return AssignedJob(
        job_id=job_id,
        docker_image_id=_whole_number(payload, "dockerImageId", job_id),
        model_id=_whole_number(payload, "modelId", job_id),
        dataset_id=_whole_number(payload, "datasetId", job_id),
        training_mode=_text(payload, "trainingMode", job_id),
        training=_training_settings(payload, job_id),
        escrow=_escrow_terms(payload, job_id),
    )


def _training_settings(payload: dict[str, Any], job_id: str) -> TrainingSettings:
    config = payload.get("trainingConfig")
    if not isinstance(config, dict):
        raise JobAssignmentError(f"assignment {job_id} carried no training settings")
    return TrainingSettings(
        epochs=_whole_number(config, "epochs", job_id),
        batch_size=_whole_number(config, "batchSize", job_id),
        learning_rate=_positive_number(config, "learningRate", job_id),
        max_length=_whole_number(config, "maxLength", job_id),
    )


def _whole_number(values: dict[str, Any], name: str, job_id: str) -> int:
    """A positive integer. ``bool`` is not one, whatever Python says.

    Every number here is either a database key or a count of something, and
    neither has a zero or a negative that means anything.
    """
    value = values.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise JobAssignmentError(
            f"assignment {job_id}: {name} is not a whole number")
    if value <= 0:
        raise JobAssignmentError(
            f"assignment {job_id}: {name} is {value}, which is not a real value")
    return value


def _positive_number(values: dict[str, Any], name: str, job_id: str) -> float:
    import math

    value = values.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise JobAssignmentError(f"assignment {job_id}: {name} is not a number")
    if not math.isfinite(value) or value <= 0:
        raise JobAssignmentError(
            f"assignment {job_id}: {name} is {value}, which is not a real value")
    return float(value)


def _text(values: dict[str, Any], name: str, job_id: str) -> str:
    value = values.get(name)
    if not isinstance(value, str) or not value.strip():
        raise JobAssignmentError(f"assignment {job_id}: {name} is missing")
    return value


#: The one cancellation reason this build acts on.
#:
#: It means the Backend refused our JOB_ACCEPTED -- the claim did not check out
#: against the chain -- and the job is not going to run. The escrow job is
#: still ASSIGNED to this machine at that point, so somebody has to give it
#: back or the client's deposit sits locked until the execution timeout.
ACCEPT_VALIDATION_FAILED = "ACCEPT_VALIDATION_FAILED"

#: A person pressed cancel. **Not handled in this build** -- see
#: :func:`read_cancellation`.
USER_REQUEST = "USER_REQUEST"


@dataclass(frozen=True)
class CancelRequest:
    """A JOB_CANCEL, read.

    ``reason`` decides everything. Only ACCEPT_VALIDATION_FAILED starts the
    on-chain cleanup below; anything else is noted and left alone, because the
    cleanup ends in ``abortJob`` and that refunds a client. Running it for a
    reason this build does not understand would give work away on a guess.
    """

    job_id: str
    reason: str

    @property
    def is_accept_validation_failure(self) -> bool:
        return self.reason == ACCEPT_VALIDATION_FAILED


def read_cancellation(message: dict[str, Any]) -> CancelRequest:
    """A JOB_CANCEL envelope as something with fields, or a refusal.

    A missing reason is **not** defaulted to ACCEPT_VALIDATION_FAILED. That
    default would make every cancellation -- including the ordinary "a customer
    changed their mind" kind -- send an ``abortJob`` this build has not been
    asked to send.
    """
    job_id = message.get("jobId")
    if not isinstance(job_id, str) or not job_id.strip():
        raise JobAssignmentError("the cancellation did not say which Job it is")

    payload = message.get("payload")
    if not isinstance(payload, dict):
        raise JobAssignmentError(f"cancellation {job_id} carried no details")

    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise JobAssignmentError(f"cancellation {job_id} gave no reason")
    return CancelRequest(job_id=job_id, reason=reason)


def job_cancelled_message(
    job_id: str,
    timestamp: str,
    *,
    reason: str,
    abort_tx_hash: str | None,
) -> dict[str, Any]:
    """Told the Backend once the escrow job is CANCELLED on chain.

    Sent **only** after the chain has been read back and says so -- either
    because an ``abortJob`` of ours landed, or because it was already cancelled
    when we looked.

    ``abortTxHash`` is omitted when no transaction was needed. There is no
    ``actualRuntimeSec`` here: nothing ran. This stage is before the GPU
    runtime exists, and inventing a zero would put a measurement in a database
    that nobody measured.
    """
    payload: dict[str, Any] = {"reason": reason}
    if abort_tx_hash:
        payload["abortTxHash"] = abort_tx_hash
    return {
        "type": JOB_CANCELLED,
        "jobId": job_id,
        "timestamp": timestamp,
        "payload": payload,
    }


def job_accepted_message(
    job_id: str,
    timestamp: str,
    *,
    escrow_job_id: int,
    operator_address: str,
    transaction_hash: str | None,
) -> dict[str, Any]:
    """The JOB_ACCEPTED envelope, in the shape ``handleJobAccepted`` reads.

    The jobId goes in the **envelope**, because that is where it is read from.
    The payload carries what the Backend needs to check the claim against the
    chain itself: which on-chain job was taken, which key took it, and the
    transaction that did it.

    An earlier build sent an empty payload. That was correct for the contract
    of the day and is not any more -- with these three fields the Backend can
    verify a claim instead of trusting it, which matters because this message
    is what moves a job to RUNNING.

    ``txHash`` is absent when the job was already ours on chain and no
    transaction was needed. The field is omitted rather than sent as null: a
    reader looking for a transaction should find nothing, not something that
    decodes to nothing.

    **No credential.** The token authenticated the handshake and the Backend
    knows which machine this session belongs to; naming ourselves again here
    would be a claim it correctly would not believe. The operator address is
    not a credential -- it is public, and it is the thing being asserted.
    """
    payload: dict[str, Any] = {
        "escrowJobId": str(escrow_job_id),
        "operatorAddress": operator_address,
    }
    if transaction_hash:
        payload["txHash"] = transaction_hash
    return {
        "type": JOB_ACCEPTED,
        "jobId": job_id,
        "timestamp": timestamp,
        "payload": payload,
    }


def job_progress_message(progress: Any, timestamp: str) -> dict[str, Any]:
    """The JOB_PROGRESS envelope, in the shape ``handleJobProgress`` reads.

    Read from ``WorkerWebSocketHandler.handleJobProgress`` and the query it
    calls, ``JobRepository.updateProgressIfRunning``:

    * ``progressPercent`` is taken with ``doubleOrNull`` into a ``DOUBLE``
      column, so it stays a real number -- 43.75, not 43;
    * **every other field is ``intOrNull``** into an ``INT`` column, and that
      includes ``elapsedSec``. The circulating example shows ``123.4``; the
      code does not, and the code is what stores the row. A float there would
      be silently truncated by ``asInt()``, which is the kind of mismatch
      that is only found when somebody wonders why a number is off.
    * the update runs only ``AND j.status = RUNNING`` and only for the worker
      the Job is assigned to. A message that arrives outside that is dropped
      by the Backend with a warning, which is its decision to make -- the
      Worker does not try to predict it and does not change its own state
      because of it.

    A field the container never reported is **left out** rather than sent as
    null. Both store a null, and the absent one says the difference between
    "not measured" and "measured as nothing".

    Nothing but numbers goes in here. No dataset, no URL, no path, no token,
    and no line of the container's output.
    """
    payload: dict[str, Any] = {
        "progressPercent": float(progress.progress_percent),
        "currentStep": int(progress.current_step),
        "totalSteps": int(progress.total_steps),
        "elapsedSec": int(progress.elapsed_sec),
    }
    if progress.current_epoch is not None:
        payload["currentEpoch"] = int(progress.current_epoch)
    if progress.total_epochs is not None:
        payload["totalEpochs"] = int(progress.total_epochs)
    return {
        "type": JOB_PROGRESS,
        "jobId": progress.job_id,
        "timestamp": timestamp,
        "payload": payload,
    }


def job_rejected_message(
    job_id: str,
    timestamp: str,
    *,
    code: str,
    message: str,
) -> dict[str, Any]:
    """The JOB_REJECTED envelope: this machine will not be taking the job.

    ``code`` is one of :class:`~gshared_worker.job_rejection.RejectCode`. It is
    the part a program reads; ``message`` is for a person looking at a log.

    Saying no is not optional politeness. Without it a Backend that assigned a
    job has to wait out a timeout to learn that nothing is coming, and the
    client's deposit sits locked for the whole of it.
    """
    return {
        "type": JOB_REJECTED,
        "jobId": job_id,
        "timestamp": timestamp,
        "payload": {"code": code, "message": message},
    }
