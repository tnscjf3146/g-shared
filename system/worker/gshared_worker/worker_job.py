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


class OfferOutcome(Enum):
    """What happened when a Job was offered to this Worker."""

    #: It was idle and now holds this Job.
    ACCEPTED = "ACCEPTED"

    #: It already holds this same Job. Nothing changed and nothing is wrong --
    #: the Backend may resend after a reconnect, and the reply is worth
    #: repeating in case the first one was lost with the old connection.
    ALREADY_HELD = "ALREADY_HELD"

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

    @property
    def held(self) -> AssignedJob | None:
        return self._held

    @property
    def is_idle(self) -> bool:
        return self._held is None

    def offer(self, job: AssignedJob) -> OfferOutcome:
        """Take this Job if the slot is free, and say what happened."""
        if self._held is None:
            self._held = job
            return OfferOutcome.ACCEPTED
        if self._held.job_id == job.job_id:
            return OfferOutcome.ALREADY_HELD
        return OfferOutcome.BUSY

    def release(self) -> None:
        """Give the slot back. Nothing in this build calls it yet.

        Here because the runtime stage will finish a Job and need it, and
        because a slot that can only be filled is not a slot.
        """
        self._held = None


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


def job_accepted_message(job_id: str, timestamp: str) -> dict[str, Any]:
    """The JOB_ACCEPTED envelope, in the shape ``handleJobAccepted`` reads.

    The jobId goes in the **envelope**, because that is where it is read from.
    The payload is empty because nothing reads it.

    **No credential.** The token authenticated the handshake and the Backend
    knows which machine this session belongs to; naming ourselves again here
    would be a claim it correctly would not believe.
    """
    return {
        "type": JOB_ACCEPTED,
        "jobId": job_id,
        "timestamp": timestamp,
        "payload": {},
    }
