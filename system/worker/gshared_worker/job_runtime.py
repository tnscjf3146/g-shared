"""Running the approved training container, and reading what it did.

This is the part that finally uses the GPU. Everything before it -- the
context, the model, the dataset, the image -- exists so that this function can
be handed inputs that have already been checked.

**The container is given nothing it does not need.** No network, a read-only
root filesystem, every capability dropped, a non-root user, and four mounts:
weights and data read-only, an output directory, and scratch space. It holds a
customer's dataset and it cannot reach the internet; it runs on a supplier's
PC and it cannot write outside its own directories.

**It is given no credential of any kind.** Not the Worker token, not the
operator key, not a registry login. It does not need them: it trains a model
and writes files. Signing and uploading happen on the host, afterwards.

**The command is a list.** Nothing here builds a shell string. Numbers come
from the execution context, which means they came from the Backend, and a
``learningRate`` of ``0.0002; rm -rf /`` is only funny until it is a
supplier's machine.

What this module deliberately does **not** do is tell the Backend anything.
The events it parses and the outcome it returns are internal; wiring them to
JOB_PROGRESS and JOB_FAILED is task 8 and task 9. Returning a value rather than
sending a message is what keeps those two tasks from having to unpick this one.
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from .job_workspace import JobWorkspace
from .logging_setup import LOGGER_NAME

_debug = logging.getLogger(LOGGER_NAME)

#: The uid the runtime image's ``USER`` line sets. The tmpfs mounts below have
#: to name it explicitly: a bare ``--tmpfs`` mounts root-owned over the
#: directory the image chowned at build time, and the container then cannot
#: write its own cache. That was found the hard way in the QLoRA smoke tests.
RUNTIME_UID = 10001
RUNTIME_GID = 10001

#: Where the container expects its inputs and writes its output.
MODEL_MOUNT = "/job/model"
INPUT_MOUNT = "/job/input"
OUTPUT_MOUNT = "/job/output"
CACHE_MOUNT = "/job/cache"

#: Resource ceilings. A training run is allowed to be slow; it is not allowed
#: to fork without limit or take the whole machine's memory.
PIDS_LIMIT = 512
MEMORY_LIMIT = "16g"
CACHE_TMPFS_SIZE = "1g"
TMP_TMPFS_SIZE = "256m"

#: What the runtime prints its events behind. One line each.
EVENT_MARKER = "GSHARED_EVENT="

#: The event names the runtime image publishes. Its contract, not ours --
#: ``system/runtime/qlora-sft/qlora_sft/events.py`` is where they are defined.
TRAINING_STARTED = "training_started"
STEP = "step"
TRAINING_COMPLETED = "training_completed"
FAILED = "failed"

#: stdout is not unbounded. A container that printed a gigabyte of warnings
#: would otherwise be a container that exhausted this process's memory.
MAX_CAPTURED_OUTPUT_BYTES = 256 * 1024

TRAINING_TIMEOUT_SECONDS = 24 * 60 * 60


class RuntimeState(Enum):
    """Where a Job is in this process. **Not** the Backend's JobStatus.

    Deliberately separate. The Backend's enum describes a row in its database
    and is its to change; this describes a container on this machine.
    """

    IDLE = "IDLE"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"
    FAILED = "FAILED"


class RuntimeFailure(Enum):
    """Why a Job did not produce an adapter.

    The first group is this Worker's: something was wrong before the container
    started. The second group mirrors the container's own exit codes, which
    are a contract the runtime image publishes and this package does not get
    to reinterpret.
    """

    CONTEXT_UNAVAILABLE = "CONTEXT_UNAVAILABLE"
    CONTEXT_MISMATCH = "CONTEXT_MISMATCH"
    MODEL_PREPARATION_FAILED = "MODEL_PREPARATION_FAILED"
    DATASET_UNAVAILABLE = "DATASET_UNAVAILABLE"
    IMAGE_REFUSED = "IMAGE_REFUSED"
    IMAGE_PULL_FAILED = "IMAGE_PULL_FAILED"
    DOCKER_UNAVAILABLE = "DOCKER_UNAVAILABLE"

    # -- the container's own exit contract ----------------------------
    INVALID_INPUT = "INVALID_INPUT"                      # 10
    MODEL_LOAD_FAILED = "MODEL_LOAD_FAILED"              # 11
    CUDA_UNAVAILABLE = "CUDA_UNAVAILABLE"                # 12
    CUDA_OOM = "CUDA_OOM"                                # 13
    TRAINING_FAILED = "TRAINING_FAILED"                  # 14
    OUTPUT_VALIDATION_FAILED = "OUTPUT_VALIDATION_FAILED"  # 15

    #: Anything else the container exited with -- 137 (killed), 1, 255. Never
    #: read as success.
    UNKNOWN_RUNTIME_EXIT = "UNKNOWN_RUNTIME_EXIT"
    #: It ran past the limit and was given up on.
    TIMED_OUT = "TIMED_OUT"


#: The runtime image's published exit codes. Changing these changes a contract
#: that lives in ``system/runtime/qlora-sft/qlora_sft/failures.py``.
EXIT_CODES: dict[int, RuntimeFailure] = {
    10: RuntimeFailure.INVALID_INPUT,
    11: RuntimeFailure.MODEL_LOAD_FAILED,
    12: RuntimeFailure.CUDA_UNAVAILABLE,
    13: RuntimeFailure.CUDA_OOM,
    14: RuntimeFailure.TRAINING_FAILED,
    15: RuntimeFailure.OUTPUT_VALIDATION_FAILED,
}


def failure_for_exit(code: int | None) -> RuntimeFailure:
    """An exit code as a named failure. Anything unrecognised is still a failure."""
    if code is None:
        return RuntimeFailure.TIMED_OUT
    return EXIT_CODES.get(int(code), RuntimeFailure.UNKNOWN_RUNTIME_EXIT)


@dataclass(frozen=True)
class RuntimeEvent:
    """One ``GSHARED_EVENT=`` line, parsed."""

    name: str
    payload: dict[str, Any]

    @property
    def step(self) -> int | None:
        return self._whole("step")

    @property
    def total_steps(self) -> int | None:
        return self._whole("totalSteps")

    @property
    def current_epoch(self) -> int | None:
        """Which epoch, as a whole number, **if the container said so**.

        Absent for a run the Trainer could not give an epoch total for. It is
        left absent rather than worked out from the step count: how many steps
        an epoch holds depends on gradient accumulation and on how the last
        batch fell, and a Worker that divides by its own guess reports a
        confident wrong number.
        """
        return self._whole("currentEpoch")

    @property
    def total_epochs(self) -> int | None:
        return self._whole("totalEpochs")

    @property
    def elapsed_sec(self) -> float | None:
        """How long the container says it has been training.

        Its clock, not ours. The Worker's own view of elapsed time starts when
        it first reads a line, which is after the image was pulled, the model
        loaded and the first batch run.
        """
        value = self.payload.get("elapsedSec")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        seconds = float(value)
        if not math.isfinite(seconds) or seconds < 0:
            return None
        return seconds

    def _whole(self, name: str) -> int | None:
        """A JSON integer, or nothing.

        ``True`` is rejected explicitly. In Python it is an ``int`` and it
        equals 1, so ``{"step": true}`` would otherwise be read as step one
        and published as real progress.
        """
        value = self.payload.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value

    @property
    def progress_percent(self) -> float | None:
        """``currentStep / totalSteps x 100``.

        Computed here because both halves are in the event and neither is
        useful alone. This module still sends it nowhere --
        :mod:`gshared_worker.runtime_progress` is what turns it into something
        the connection loop can publish.
        """
        step, total = self.step, self.total_steps
        if step is None or not total:
            return None
        return round(step / total * 100.0, 2)


@dataclass
class RuntimeOutcome:
    """What happened to one Job, for task 9 to turn into a message.

    ``abort`` is left empty here. Giving a job back on chain is
    :mod:`gshared_worker.escrow`'s, and whether to do it is a decision the
    caller makes -- this records what happened, not what should follow.
    """

    state: RuntimeState
    failure: RuntimeFailure | None = None
    detail: str = ""
    exit_code: int | None = None
    output_dir: Path | None = None
    events: list[RuntimeEvent] = field(default_factory=list)
    #: Filled in by whoever ran an abortJob after a failure, so task 9 can
    #: report both facts in one JOB_FAILED.
    abort_tx_hash: str | None = None
    #: How long the training container ran, in seconds, measured with a
    #: monotonic clock. **The authoritative ``actualRuntimeSec``.**
    #:
    #: ``None`` when no container ever started -- a context that did not
    #: match, a dataset that would not download. Those failures took time too,
    #: but none of it was runtime, and a zero here would be a measurement that
    #: was never made. The Backend's column is nullable; it stays null.
    #:
    #: Deliberately **not** the container's own ``runtimeSec``. That one
    #: starts when the Trainer does, so it leaves out loading the model, which
    #: on a cold machine is most of a short job. The Scheduler is predicting
    #: how long a supplier's GPU will be busy, and the supplier's GPU is busy
    #: for all of it.
    actual_runtime_sec: float | None = None

    @property
    def succeeded(self) -> bool:
        return self.state is RuntimeState.FINISHED

    @property
    def last_event(self) -> RuntimeEvent | None:
        return self.events[-1] if self.events else None


def parse_events(stdout: str) -> list[RuntimeEvent]:
    """Every well-formed event line, and nothing else.

    A malformed line is skipped rather than raised on. The container's stdout
    is shared with PyTorch, bitsandbytes and CUDA, all of which write whatever
    they like; a training run that finished must not be reported as failed
    because a warning happened to start with the marker.
    """
    events: list[RuntimeEvent] = []
    for line in (stdout or "").splitlines():
        event = parse_event_line(line)
        if event is not None:
            events.append(event)
    return events


def parse_event_line(line: str) -> RuntimeEvent | None:
    """One line of the container's stdout, or None if it was not an event.

    **The one place a line becomes an event.** Reading the whole of stdout
    after the container exits and reading it a line at a time while it runs
    are two callers of this function, so the same text cannot mean one thing
    live and another in the record -- which it would, sooner or later, if
    each had grown its own copy of the rules.

    A malformed line is skipped rather than raised on. The container's stdout
    is shared with PyTorch, bitsandbytes and CUDA, all of which write whatever
    they like; a training run that finished must not be reported as failed
    because a warning happened to start with the marker.
    """
    position = line.find(EVENT_MARKER)
    if position < 0:
        return None
    try:
        payload = json.loads(line[position + len(EVENT_MARKER):])
    except ValueError:
        _debug.info("runtime: an event line was not JSON")
        return None
    if not isinstance(payload, dict):
        return None
    name = payload.get("event")
    if not isinstance(name, str) or not name:
        return None
    return RuntimeEvent(name, payload)


def training_arguments(training: Any) -> list[str]:
    """The runtime's CLI, from the context's numbers.

    Numbers only. The container has never heard of SPEED or BALANCED -- those
    are the AI Scheduler's names for sets of numbers, and resolving them is
    done long before this point.
    """
    check_training(training)
    return [
        "--model-path", MODEL_MOUNT,
        "--dataset-path", f"{INPUT_MOUNT}/dataset.jsonl",
        "--output-dir", OUTPUT_MOUNT,
        "--epochs", str(int(training.epochs)),
        "--batch-size", str(int(training.batch_size)),
        "--learning-rate", repr(float(training.learning_rate)),
        "--max-length", str(int(training.max_length)),
    ]


def check_training(training: Any) -> None:
    """Typed and in range before it is turned into text.

    The context parser already refuses a non-number. This is the second check,
    at the point of use, because this is where the values stop being data and
    become a command line.
    """
    import math

    for name, value in (("epochs", training.epochs),
                        ("batchSize", training.batch_size),
                        ("maxLength", training.max_length)):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"trainingConfig.{name} is not a usable number")
    rate = training.learning_rate
    if (isinstance(rate, bool) or not isinstance(rate, (int, float))
            or not math.isfinite(float(rate)) or float(rate) <= 0):
        raise ValueError("trainingConfig.learningRate is not a usable number")


def container_name(job_id: str) -> str:
    """One deterministic name per Job, from an id that has been checked."""
    from .job_workspace import safe_job_id

    return f"gshared-job-{safe_job_id(job_id)}"


def run_command(
    image_reference: str,
    workspace: JobWorkspace,
    model_path: Path,
    training: Any,
    *,
    host_path_of: Callable[[Path], Any] | None = None,
) -> list[str]:
    """The whole ``docker run``, as a list of arguments.

    Every flag here was chosen against something specific:

    * ``--network none`` -- the container holds a customer's dataset;
    * ``--read-only`` plus explicit mounts -- it cannot alter the weights it
      was given, or anything outside its own output;
    * ``--cap-drop ALL`` and ``--security-opt no-new-privileges`` -- training
      needs no privileges, and an image that later tries to gain some fails;
    * ``--pids-limit`` and ``--memory`` -- a runaway process is contained;
    * ``uid``/``gid`` on the tmpfs mounts -- without them the mount is
      root-owned and the non-root runtime cannot write its cache.

    No ``-e`` carries a secret. The only environment set is a cache path.
    """
    where = host_path_of or (lambda path: path)
    return [
        "docker", "run", "--rm",
        "--name", container_name(workspace.job_id),
        "--gpus", "all",
        "--network", "none",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", str(PIDS_LIMIT),
        "--memory", MEMORY_LIMIT,
        "--user", f"{RUNTIME_UID}:{RUNTIME_GID}",
        "--tmpfs", f"/tmp:rw,noexec,nosuid,size={TMP_TMPFS_SIZE},"
                   f"uid={RUNTIME_UID},gid={RUNTIME_GID},mode=0700",
        "--tmpfs", f"{CACHE_MOUNT}:rw,nosuid,size={CACHE_TMPFS_SIZE},"
                   f"uid={RUNTIME_UID},gid={RUNTIME_GID},mode=0700",
        "-v", f"{where(model_path)}:{MODEL_MOUNT}:ro",
        "-v", f"{where(workspace.input_dir)}:{INPUT_MOUNT}:ro",
        "-v", f"{where(workspace.output_dir)}:{OUTPUT_MOUNT}",
        image_reference,
        *training_arguments(training),
    ]


async def run_training(
    runner: Any,
    image_reference: str,
    workspace: JobWorkspace,
    model_path: Path,
    training: Any,
    *,
    host_path_of: Callable[[Path], Any] | None = None,
    timeout: float = TRAINING_TIMEOUT_SECONDS,
    on_event: Callable[[RuntimeEvent], None] | None = None,
    monotonic: Callable[[], float] | None = None,
) -> RuntimeOutcome:
    """Start the container, watch it, and say what happened.

    Success is an exit code of zero **and** an output directory with something
    in it. The container validates its own output and exits 15 when it is
    wrong, so this is the outer of two checks rather than the only one.

    ``on_event`` is called **while the container is still running**, once per
    event line, on whatever thread is running this coroutine. It is how
    progress leaves this module; it is not how progress leaves the Worker.
    Nothing here touches a socket -- see
    :mod:`gshared_worker.runtime_progress`.

    A runner without ``run_streaming`` still works: the events are then read
    from the finished output, exactly as before this was written. That path
    is the fallback, not the design -- the whole point is that a step event
    reaches the caller minutes before the container exits.
    """
    command = run_command(image_reference, workspace, model_path, training,
                          host_path_of=host_path_of)
    _debug.info("runtime: starting %s", container_name(workspace.job_id))

    events: list[RuntimeEvent] = []
    tick = monotonic or time.monotonic
    # The clock starts here and stops when the process is gone: container
    # start, the model load inside it, the training, its own output check,
    # and termination. Everything before this line -- the context, the
    # weights, the dataset, the pull -- already happened, and everything
    # after it, the packaging and the upload and the chain, has not.
    started_at = tick()
    try:
        finished = await _run_container(
            runner, command, timeout, events, on_event)
    except Exception as error:
        return RuntimeOutcome(
            RuntimeState.FAILED, RuntimeFailure.DOCKER_UNAVAILABLE,
            f"the training container could not be started "
            f"({type(error).__name__})",
            actual_runtime_sec=_elapsed(tick, started_at))

    ran_for = _elapsed(tick, started_at)

    if finished is None:
        return RuntimeOutcome(
            RuntimeState.FAILED, RuntimeFailure.TIMED_OUT,
            "the training container did not finish in time",
            actual_runtime_sec=ran_for)

    if not events:
        # Either the fallback path, or a container that said nothing at all.
        events = parse_events(finished.stdout or "")
    exit_code = int(finished.exit_code)

    if exit_code != 0:
        failure = failure_for_exit(exit_code)
        _debug.info("runtime: exit %s -- %s", exit_code, failure.value)
        return RuntimeOutcome(
            RuntimeState.FAILED, failure,
            _said_why(events, failure), exit_code,
            workspace.output_dir, events, actual_runtime_sec=ran_for)

    if not _has_output(workspace.output_dir):
        # Exit 0 with nothing written. The container checks this itself, so
        # reaching here means its own check did not run.
        return RuntimeOutcome(
            RuntimeState.FAILED, RuntimeFailure.OUTPUT_VALIDATION_FAILED,
            "the container finished successfully but wrote no adapter",
            exit_code, workspace.output_dir, events,
            actual_runtime_sec=ran_for)

    return RuntimeOutcome(
        RuntimeState.FINISHED, None, "training finished", exit_code,
        workspace.output_dir, events, actual_runtime_sec=ran_for)


async def _run_container(
    runner: Any,
    command: list[str],
    timeout: float,
    events: list[RuntimeEvent],
    on_event: Callable[[RuntimeEvent], None] | None,
) -> Any:
    """Run it, collecting events live when the runner can do that.

    Only stdout is parsed. stderr is drained -- it has to be, or the container
    blocks on a full pipe -- but nothing in it is read as an event and none of
    it is forwarded anywhere. It is where a stack trace with a path in it
    would appear.
    """
    streaming = getattr(runner, "run_streaming", None)
    if streaming is None:
        return await runner.run(
            command, timeout=timeout,
            max_output_bytes=MAX_CAPTURED_OUTPUT_BYTES)

    def saw(line: str) -> None:
        event = parse_event_line(line)
        if event is None:
            return
        events.append(event)
        if on_event is not None:
            on_event(event)

    return await streaming(
        command, timeout=timeout,
        max_output_bytes=MAX_CAPTURED_OUTPUT_BYTES,
        on_stdout_line=saw)


def _elapsed(tick: Callable[[], float], started_at: float) -> float:
    """Whole and fractional seconds, never negative.

    ``time.monotonic`` cannot go backwards, so the floor is for the injected
    clocks in tests -- and for the one number that must never reach the
    Backend as a negative duration.
    """
    return max(0.0, round(tick() - started_at, 3))


def _has_output(output_dir: Path) -> bool:
    """The two files an adapter is. Task 9 packages them; this only looks."""
    return ((output_dir / "adapter_config.json").is_file()
            and (output_dir / "adapter_model.safetensors").is_file())


def _said_why(events: list[RuntimeEvent], failure: RuntimeFailure) -> str:
    """The container's own last word, if it left one.

    Its ``failed`` event carries a message written to be shown, unlike a
    traceback. Only that message is used -- never raw stderr, which can carry
    a path or a fragment of somebody's dataset.
    """
    for event in reversed(events):
        if event.name == "failed":
            message = event.payload.get("message")
            if isinstance(message, str) and message:
                return message
    return f"the training container failed ({failure.value})"
