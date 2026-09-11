"""Stopping the training container -- ours, and only ours.

Two things have to be true before a container is stopped, and the second is
the one that takes work.

**It has to be stopped properly.** ``docker stop`` sends SIGTERM, waits, then
sends SIGKILL, and the wait is bounded here rather than left to Docker's
default so a cancellation cannot hang on a container that ignores signals.
``docker kill`` is not used first: a training process given a moment to end
leaves its files closed.

**It has to be ours.** The container's name is derived from the job id, so a
container left behind by an earlier run of the same Job -- or started by a
different Worker process on this machine -- wears the same one. Stopping by
name alone is stopping whatever happens to be wearing it. So the labels this
Worker puts on its own containers are read back first, and a container whose
labels do not match the handle in hand is left alone.

**Nothing is abandoned as "probably stopped".** The answer this module returns
is what the escrow decides on: an ``abortJob`` sent while the GPU is still
training would refund the client and leave the machine doing the work anyway.
:class:`StopOutcome.STILL_RUNNING` is a refusal, not a warning.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .job_runtime import LABEL_GENERATION, LABEL_JOB_ID, container_name
from .job_workspace import safe_job_id
from .logging_setup import LOGGER_NAME

_debug = logging.getLogger(LOGGER_NAME)

#: How long the container is given to end on its own before it is killed.
#:
#: The training loop has no cancellation handler and will not shut down
#: gracefully; this is the time for the process to be torn down, files closed
#: and the CUDA context released. Long enough not to leave a wedged GPU,
#: short enough that a person watching does not think it hung.
STOP_GRACE_SECONDS = 20

#: Bounds on the two commands themselves. The stop's own budget is its grace
#: period plus room for Docker to do the tearing down.
STOP_TIMEOUT_SECONDS = STOP_GRACE_SECONDS + 40
INSPECT_TIMEOUT_SECONDS = 30

MAX_OUTPUT_BYTES = 64 * 1024


class StopOutcome(Enum):
    """What is known about the container now. Not what was attempted."""

    #: It was ours, it was running, and it is not any more.
    STOPPED = "STOPPED"
    #: It had already ended -- naturally, or on an earlier stop. With
    #: ``--rm`` this is also what a container that ended looks like: gone.
    ALREADY_GONE = "ALREADY_GONE"
    #: Something is there under that name and it is **not** this run's.
    #: Nothing was sent to it.
    NOT_OURS = "NOT_OURS"
    #: It is still running. The escrow must not be aborted.
    STILL_RUNNING = "STILL_RUNNING"
    #: Docker could not be asked. Also not a reason to abort anything.
    UNKNOWN = "UNKNOWN"

    @property
    def is_stopped(self) -> bool:
        """Whether the GPU is definitely free of this Job."""
        return self in (StopOutcome.STOPPED, StopOutcome.ALREADY_GONE)


@dataclass
class RuntimeHandle:
    """The one container this Worker started for this Job, and when.

    Made when the ``docker run`` goes out and kept for as long as the Job is
    held. It is what makes "stop the container" a statement about a specific
    process rather than about a name.
    """

    job_id: str
    generation: int
    container_name: str
    #: Set by whoever started it, from the same monotonic clock the runtime
    #: measures with. Used only for logging here.
    started_at: float | None = None
    stop_requested: bool = False
    outcome: StopOutcome | None = None

    @classmethod
    def for_job(cls, job_id: str, generation: int) -> "RuntimeHandle":
        return cls(job_id=job_id, generation=generation,
                   container_name=container_name(job_id))


@dataclass(frozen=True)
class ContainerState:
    """What ``docker inspect`` said, as far as this module cares."""

    exists: bool
    running: bool = False
    labels: dict[str, str] = field(default_factory=dict)

    def belongs_to(self, handle: RuntimeHandle) -> bool:
        """Whether this really is the container that handle names.

        Both labels, both matching. A container with no labels at all is not
        ours -- every container this Worker starts is labelled, so an
        unlabelled one under our name came from somewhere else.
        """
        return (self.labels.get(LABEL_JOB_ID) == safe_job_id(handle.job_id)
                and self.labels.get(LABEL_GENERATION) == str(handle.generation))


#: What separates the two fields in the inspect format.
#:
#: A **real tab**, not the two characters backslash and ``t``. Docker
#: hands the format string to Go's template engine, which emits an
#: unrecognised escape literally -- so a backslash-t here produces the
#: text itself in the output, :func:`read_state` finds no separator,
#: reads no labels, and every container looks like somebody else's.
#:
#: The first real GPU cancellation ran to completion because of exactly
#: that: the stop answered NOT_OURS about the container this process had
#: started a moment earlier, so nothing was stopped. The unit tests did
#: not catch it -- they built the inspect output with a real tab of their
#: own and never put this format string through the parser.
FIELD_SEPARATOR = "\t"


def inspect_command(name: str) -> list[str]:
    """Ask for the two facts that decide everything, and nothing else."""
    return ["docker", "inspect", "--format",
            "{{.State.Running}}" + FIELD_SEPARATOR + "{{json .Config.Labels}}",
            name]


def stop_command(name: str, grace: int = STOP_GRACE_SECONDS) -> list[str]:
    """SIGTERM, ``grace`` seconds, then SIGKILL -- Docker's own sequence.

    The timeout is given explicitly rather than left at Docker's ten-second
    default, so the wait is a number this repository chose and can change in
    one place.
    """
    return ["docker", "stop", "--time", str(int(grace)), name]


def read_state(output: str) -> ContainerState:
    """``docker inspect``'s answer, or a container that is not there.

    An inspect that failed is reported as *not existing* only by the caller,
    which knows the exit code. This parses a successful one.
    """
    line = (output or "").strip().splitlines()
    if not line:
        return ContainerState(exists=False)
    running, _, labels_json = line[0].partition(FIELD_SEPARATOR)
    try:
        labels = json.loads(labels_json) if labels_json.strip() else {}
    except ValueError:
        labels = {}
    if not isinstance(labels, dict):
        labels = {}
    return ContainerState(
        exists=True,
        running=running.strip().lower() == "true",
        labels={str(k): str(v) for k, v in labels.items() if v is not None},
    )


async def look_at_container(runner: Any, handle: RuntimeHandle) -> ContainerState:
    """What Docker currently holds under this handle's name."""
    found = await runner.run(
        inspect_command(handle.container_name),
        timeout=INSPECT_TIMEOUT_SECONDS, max_output_bytes=MAX_OUTPUT_BYTES)
    if found is None or found.exit_code != 0:
        # "No such object" is the ordinary case here, not an error: with
        # ``--rm`` a container that ended has already been removed.
        return ContainerState(exists=False)
    return read_state(found.stdout or "")


async def stop_container(
    runner: Any,
    handle: RuntimeHandle,
    *,
    grace: int = STOP_GRACE_SECONDS,
) -> StopOutcome:
    """Stop this run's container, and say what is true afterwards.

    The order matters. It looks first, because the only safe reason to send a
    stop is having just seen our own labels on a running container. It looks
    again afterwards, because a stop command that returned zero is Docker
    saying it tried -- the escrow decision needs Docker saying it is gone.
    """
    try:
        before = await look_at_container(runner, handle)
    except Exception as error:
        _debug.info("stop: %s could not be inspected (%s)",
                    handle.container_name, type(error).__name__)
        return StopOutcome.UNKNOWN

    if not before.exists:
        # Never started, already ended, or ended and removed by ``--rm``.
        # All three mean the GPU is not running this Job.
        _debug.info("stop: %s is not there", handle.container_name)
        handle.outcome = StopOutcome.ALREADY_GONE
        return StopOutcome.ALREADY_GONE

    if not before.belongs_to(handle):
        # The name matched and the labels did not. Somebody else's.
        _debug.info("stop: %s is not this run's container; left alone",
                    handle.container_name)
        handle.outcome = StopOutcome.NOT_OURS
        return StopOutcome.NOT_OURS

    if not before.running:
        handle.outcome = StopOutcome.ALREADY_GONE
        return StopOutcome.ALREADY_GONE

    handle.stop_requested = True
    _debug.info("stop: asking %s to stop (%ss grace)",
                handle.container_name, grace)
    try:
        await runner.run(
            stop_command(handle.container_name, grace),
            timeout=STOP_TIMEOUT_SECONDS, max_output_bytes=MAX_OUTPUT_BYTES)
    except Exception as error:
        # The command failed. That says nothing about the container, so the
        # answer comes from looking rather than from the exit code.
        _debug.info("stop: the stop command failed (%s)", type(error).__name__)

    try:
        after = await look_at_container(runner, handle)
    except Exception as error:
        _debug.info("stop: %s could not be re-inspected (%s)",
                    handle.container_name, type(error).__name__)
        handle.outcome = StopOutcome.UNKNOWN
        return StopOutcome.UNKNOWN

    if not after.exists:
        handle.outcome = StopOutcome.STOPPED
        return StopOutcome.STOPPED
    if not after.belongs_to(handle):
        # Gone and replaced between the two looks. Ours is no longer running.
        handle.outcome = StopOutcome.STOPPED
        return StopOutcome.STOPPED
    if after.running:
        _debug.info("stop: %s is still running after the grace period",
                    handle.container_name)
        handle.outcome = StopOutcome.STILL_RUNNING
        return StopOutcome.STILL_RUNNING

    handle.outcome = StopOutcome.STOPPED
    return StopOutcome.STOPPED
