"""What the container says while it works, in a form a program can read.

JOB_PROGRESS is task 8 and is not implemented here. What *is* implemented is
the thing task 8 needs in order not to be written twice: a stdout contract, so
the Worker parses JSON rather than guessing at English sentences printed by
somebody else's training loop.

Every event is one line::

    GSHARED_EVENT={"event":"step","step":7,"totalSteps":100,"epoch":0.21}

The marker is there because stdout is shared. ``transformers`` writes progress
bars, ``bitsandbytes`` writes a banner, CUDA writes warnings -- a reader that
took every line as JSON would drown. This is the same convention the GPU
benchmark already uses for its result line.

Progress is reported as ``step`` out of ``totalSteps`` and never as an epoch
fraction alone, because the percentage task 8 will show is
``currentStep / totalSteps``. An epoch count cannot answer that question when
one epoch is four hundred steps.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable

#: One line, so an event survives whatever else the container writes.
EVENT_MARKER = "GSHARED_EVENT="

#: Bumped when the shape of these events changes in a way a reader would
#: notice. A reader that does not recognise the version should refuse the
#: stream rather than guess at it.
EVENT_SCHEMA_VERSION = "qlora-sft-events-v1"

TRAINING_STARTED = "training_started"
STEP = "step"
TRAINING_COMPLETED = "training_completed"
FAILED = "failed"


def format_event(name: str, **fields: Any) -> str:
    """One event line, without the newline.

    ``allow_nan=False`` is not decoration. A loss that has gone to NaN is
    exactly when a training run most wants to report something, and bare
    ``NaN`` is not JSON -- a reader on the other side would fail to parse the
    one line that mattered. Non-finite numbers are turned into null here so
    the line stays readable and the reader can see the loss was not a number.
    """
    payload = {"event": name, "version": EVENT_SCHEMA_VERSION}
    payload.update({key: _plain(value) for key, value in fields.items()})
    return EVENT_MARKER + json.dumps(
        payload, separators=(",", ":"), allow_nan=False, ensure_ascii=False)


def _plain(value: Any) -> Any:
    """Floats that JSON can carry, and everything else untouched."""
    if isinstance(value, float):
        # NaN and the infinities are real outcomes of a diverging run.
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return round(value, 6)
    return value


class EventWriter:
    """Writes events to stdout, or wherever a test points it.

    Flushing every line is the point of the class. Docker gives the Worker
    this stream through a pipe, and a block-buffered pipe would hold a
    training run's entire progress until the container exited -- which is the
    one moment progress is no longer interesting.
    """

    def __init__(self, out: Callable[[str], Any] | None = None) -> None:
        self._out = out if out is not None else _to_stdout

    def emit(self, name: str, **fields: Any) -> None:
        self._out(format_event(name, **fields))

    def training_started(self, *, total_steps: int, **fields: Any) -> None:
        self.emit(TRAINING_STARTED, totalSteps=total_steps, **fields)

    def step(self, *, step: int, total_steps: int, epoch: float,
             loss: float | None = None) -> None:
        self.emit(STEP, step=step, totalSteps=total_steps, epoch=epoch,
                  loss=loss)

    def training_completed(self, *, runtime_sec: float, **fields: Any) -> None:
        self.emit(TRAINING_COMPLETED, runtimeSec=runtime_sec, **fields)

    def failed(self, *, error_code: str, message: str) -> None:
        self.emit(FAILED, errorCode=error_code, message=message)


def _to_stdout(line: str) -> None:
    print(line, flush=True)
