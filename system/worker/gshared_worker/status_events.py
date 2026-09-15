"""What the Worker is doing, for whoever is watching -- as values, never widgets.

The packaged Worker has no console, so everything it says would otherwise go
nowhere. This module is the one way the Worker's own code tells a watcher what
is happening, and it knows nothing about who the watcher is: the status window
(:mod:`gshared_worker.gui.status_window`) installs a queue here and reads it on
its own thread; a Worker run from a terminal installs nothing and every publish
costs a function call.

Same shape as :mod:`logging`, and for the same reason. The code that knows a
connection just registered is deep in :mod:`gshared_worker.worker_connection`;
threading a window through every stage to reach it would couple the Worker to
a screen, and every test that drives a stage with ``(settings, say)`` would have
to learn about it.

**Values cross the thread boundary, nothing else.** Every event is a frozen
dataclass. Nothing here holds a widget, a socket or a Job, and nothing here
calls back into the Worker. Publishing never raises: a watcher that broke must
not stop a training run.

Three kinds of state are kept apart on purpose:

* :class:`WorkerStage` -- where this process is in its own run. Published by
  the Worker.
* :class:`JobStage` -- what the Job slot holds. Published by the connection
  loop, from the objects it already owns.
* What the window shows -- decided by the presenter from both, and never
  published by the Worker. ``STOPPING`` and ``ERROR`` are things a window
  says, not states the Worker is in.

The Backend's view of this machine (ONLINE / OFFLINE) is the Backend's and is
not modelled here. ``WorkerStage.CONNECTED`` means *this* process holds a
registered connection, which is the part the Worker can know; the window may
call that "온라인", the Worker does not.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from .logging_setup import LOGGER_NAME

_debug = logging.getLogger(LOGGER_NAME)


class WorkerStage(Enum):
    """Where this Worker process is in its run, in the order it gets there."""

    STARTING = "STARTING"
    ENVIRONMENT_CHECK = "ENVIRONMENT_CHECK"
    RUNTIME_PREPARING = "RUNTIME_PREPARING"
    AUTHENTICATING = "AUTHENTICATING"
    #: No usable identity: a person has to sign in. Also where the stale-token
    #: recovery lands, after the dead identity has been forgotten.
    REGISTRATION_REQUIRED = "REGISTRATION_REQUIRED"
    CONNECTING = "CONNECTING"
    #: This process holds a registered connection and is beating on it. Named
    #: for what the Worker knows; whether the Backend counts it online is the
    #: Backend's to say.
    CONNECTED = "CONNECTED"
    #: The connection went away and another is being opened.
    RECONNECTING = "RECONNECTING"


class JobStage(Enum):
    """What the one Job slot holds, as far as a watcher needs to know."""

    IDLE = "IDLE"
    #: Offered and being accepted on chain. Nothing is running yet.
    ACCEPTING = "ACCEPTING"
    #: Accepted. Being prepared, trained or settled. Not the Backend's RUNNING,
    #: which is a status of the Job's row there.
    WORKING = "WORKING"
    #: The customer asked for it to stop and that is under way.
    CANCELLING = "CANCELLING"


class JobEnd(Enum):
    """How a Job ended, once the Backend has been told."""

    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class StageChanged:
    stage: WorkerStage


@dataclass(frozen=True)
class Said:
    """One line the Worker said to the supplier. The same line ``say`` prints."""

    text: str


@dataclass(frozen=True)
class GpuDescribed:
    model: str
    vram_mb: int | None = None


@dataclass(frozen=True)
class WorkerOnline:
    """A connection registered. The id is shortened before anything shows it."""

    worker_id: str


@dataclass(frozen=True)
class JobChanged:
    """The slot's contents changed. ``job_id`` is None when it is empty."""

    job_id: str | None
    stage: JobStage
    #: 0-100, only while training and only from a real progress event.
    percent: float | None = None


@dataclass(frozen=True)
class JobEnded:
    job_id: str
    end: JobEnd


class StatusSink(Protocol):
    def publish(self, event: Any) -> None: ...


class _Nobody:
    """What is installed when nobody is watching. Publishing costs nothing."""

    def publish(self, event: Any) -> None:
        return None


_lock = threading.Lock()
_sink: StatusSink = _Nobody()


def install(sink: StatusSink | None) -> StatusSink:
    """Make ``sink`` the watcher. Returns the previous one, to put back later."""
    global _sink
    with _lock:
        previous = _sink
        _sink = sink if sink is not None else _Nobody()
        return previous


def publish(event: Any) -> None:
    """Tell whoever is watching. Never raises, from any thread."""
    with _lock:
        sink = _sink
    try:
        sink.publish(event)
    except Exception:
        # A watcher that failed is not the Worker's failure.
        _debug.debug("status: an event could not be published", exc_info=True)


class StatusQueue:
    """The bridge a window reads: publish from any thread, drain on its own.

    Unbounded on purpose. A window that stopped draining is a window that is
    closing, and dropping a line then would be dropping the one that says why.
    The window keeps only the last few hundred lines it has *shown*.
    """

    def __init__(self) -> None:
        self._queue: "queue.Queue[Any]" = queue.Queue()

    def publish(self, event: Any) -> None:
        self._queue.put(event)

    def drain(self, limit: int = 500) -> list[Any]:
        """Everything waiting, up to ``limit``. Never blocks."""
        taken: list[Any] = []
        while len(taken) < limit:
            try:
                taken.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return taken


# -- what may be shown -------------------------------------------------------

_JWT = re.compile(r"eyJ[\w-]{4,}\.[\w-]{4,}\.[\w-]{4,}")
_BEARER = re.compile(r"(?i)\bbearer\s+\S+")
_URL_QUERY = re.compile(r"(https?://[^\s?#]+)\?\S*")
_HEX_64 = re.compile(r"\b(?:0x)?[0-9a-fA-F]{64}\b")
_ADDRESS = re.compile(r"\b0x[0-9a-fA-F]{40}\b")
_WORKER_ID = re.compile(r"\bworker-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b")
#: Anything long and opaque: a token, a sealed blob, a signature.
_OPAQUE = re.compile(r"[A-Za-z0-9_\-+/=]{40,}")

HIDDEN = "[숨김]"


def redact(text: str) -> str:
    """A line fit for a screen.

    The Worker's own lines already carry no secret -- that is a rule every
    ``say`` in this package keeps. This is the second line of defence, for a
    detail that came from a library or a service: a JWT, a bearer header, a
    signed URL's query, a private key or anything else long and opaque is
    hidden; an address or a Worker id is shortened to something recognisable.
    """
    if not text:
        return ""
    text = _JWT.sub(HIDDEN, text)
    text = _BEARER.sub(HIDDEN, text)
    text = _URL_QUERY.sub(lambda m: f"{m.group(1)}?{HIDDEN}", text)
    text = _HEX_64.sub(HIDDEN, text)
    text = _ADDRESS.sub(lambda m: shorten(m.group(0), 6, 4), text)
    text = _WORKER_ID.sub(lambda m: shorten(m.group(0), 11, 4), text)
    text = _OPAQUE.sub(HIDDEN, text)
    return text


def shorten(value: str, head: int = 11, tail: int = 4) -> str:
    """``worker-1b76…903f``: enough to recognise, not the whole identifier."""
    if not value or len(value) <= head + tail + 1:
        return value or ""
    return f"{value[:head]}…{value[-tail:]}"
