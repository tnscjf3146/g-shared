"""Turning a container's step events into something the connection can send.

The container writes progress to stdout. The Backend wants it on a WebSocket.
Between the two there is a thread boundary and a set of rules, and this module
is both.

::

    container stdout (background thread)
        -> RuntimeEvent           job_runtime.parse_event_line
        -> RuntimeProgressEvent   ProgressTracker  -- validates, orders
        -> latest snapshot        ProgressChannel  -- coalesces, one slot
        -> JOB_PROGRESS           worker_connection, on the loop thread

**Nothing here touches a socket.** The training runs on a background thread
and the WebSocket has exactly one owner; a progress update that sent itself
would be a second writer on that connection, interleaved with a heartbeat.
What crosses the boundary is a value in a box.

**The box holds one.** Progress is not a log -- nobody needs step 101 once
step 103 has happened, and a Worker that queued every step would spend a
reconnection sending history instead of the truth. A run that produced ten
thousand events and was read once publishes one number.

**The terminal outcome does not come through here.** Whether a Job succeeded
is in :class:`~gshared_worker.job_runtime.RuntimeOutcome`, on its own queue,
and it is never dropped or coalesced. Losing a progress update costs a
percentage that will be right again in two seconds; losing the outcome loses
the Job.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from .job_runtime import (
    FAILED,
    STEP,
    TRAINING_COMPLETED,
    TRAINING_STARTED,
    RuntimeEvent,
)
from .logging_setup import LOGGER_NAME

_debug = logging.getLogger(LOGGER_NAME)


@dataclass(frozen=True)
class RuntimeProgressEvent:
    """How far one training run has got. Internal -- not a WebSocket payload.

    Every field is a number the container measured or the Worker already
    knew. There is nothing here that came from a guess, and nothing that came
    from anywhere near a dataset, a URL or a credential.

    ``generation`` is this Worker's own count of training runs, and never
    leaves the process. It is what lets a late line from a container that has
    already exited be recognised as late: the job id alone cannot do that,
    because the second run of a Job carries the same one.
    """

    job_id: str
    generation: int
    progress_percent: float
    current_step: int
    total_steps: int
    elapsed_sec: int
    current_epoch: int | None = None
    total_epochs: int | None = None


class ProgressTracker:
    """Watches one training run and publishes what is worth publishing.

    One tracker per container, made by whoever starts it. It holds the run's
    generation, the clock it measures against, and how far it has already
    reported -- all of which are per-run, which is why this is an object
    rather than a function.
    """

    def __init__(
        self,
        job_id: str,
        generation: int,
        channel: "ProgressChannel",
        *,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._job_id = job_id
        self._generation = generation
        self._channel = channel
        # Monotonic, never the wall clock. ``datetime.now()`` moves when the
        # host syncs its time or crosses a DST boundary, and a duration
        # measured with it can come out negative on a machine that was simply
        # corrected by half a second.
        self._monotonic = monotonic or time.monotonic
        self._started_at: float | None = None
        self._total_steps: int | None = None
        self._total_epochs: int | None = None
        self._last_step = 0
        self._last_elapsed = 0
        self._running = False
        self._closed = False

    @property
    def generation(self) -> int:
        return self._generation

    def observe(self, event: RuntimeEvent) -> None:
        """One event from the container. Called on the runtime thread.

        Never raises. This runs inside the pipe reader, and an exception here
        would stop the pipe being drained -- which stops the container, for
        the sake of a progress number.
        """
        try:
            self._observe(event)
        except Exception:
            _debug.exception("progress: an event could not be handled")

    def _observe(self, event: RuntimeEvent) -> None:
        if self._closed:
            return
        if event.name == TRAINING_STARTED:
            self._begin(event)
            return
        if event.name == STEP:
            self._advance(event)
            return
        if event.name == TRAINING_COMPLETED:
            self._finish(event)
            return
        if event.name == FAILED:
            # A failure is not progress. It belongs to the outcome, which task
            # 9 turns into JOB_FAILED; dressing it up as a percentage would
            # tell the customer the run was going fine.
            self._closed = True

    # -- the three things that happen ---------------------------------
    def _begin(self, event: RuntimeEvent) -> None:
        """Training has started. Nothing is published yet.

        There is no step to report and no honest way to invent one. A
        ``currentStep`` of 0 against a ``totalSteps`` of 0 is not "0%", it is
        two numbers made up to fill a shape, and the Backend would store them.
        The first real step is a couple of seconds away.
        """
        self._started_at = self._monotonic()
        self._total_steps = _positive(event.total_steps)
        self._total_epochs = _positive(event.payload.get("epochs"))
        self._running = True

    def _advance(self, event: RuntimeEvent) -> None:
        if not self._running:
            # A step before training_started. Either the events arrived out of
            # order or something else on stdout is imitating them; either way
            # this run has not been told how long it is.
            _debug.info("progress: a step arrived before training started")
            return
        published = self._measure(event.step, event.total_steps, event)
        if published is not None:
            self._channel.publish(published)

    def _finish(self, event: RuntimeEvent) -> None:
        """The container says training completed. Report the last step as done.

        Only the *progress* reaches 100 here. Whether the Job succeeded is
        decided by the exit code and the files on disk, in
        :func:`~gshared_worker.job_runtime.run_training`, and a container can
        publish this event and then fail to write its adapter.
        """
        total = _positive(event.total_steps) or self._total_steps
        if self._running and total:
            published = self._measure(total, total, event)
            if published is not None:
                self._channel.publish(published)
        self._closed = True
        self._running = False

    # -- the rules ----------------------------------------------------
    def _measure(
        self, step: Any, total: Any, event: RuntimeEvent
    ) -> RuntimeProgressEvent | None:
        """One validated, ordered progress event -- or nothing at all.

        Nothing at all is a normal outcome. A step that repeats, a step that
        goes backwards, a percentage outside 0-100: each of those is dropped
        here rather than clamped into range, because a clamp turns a broken
        event into a plausible one and the fault disappears.
        """
        total = _positive(total) or self._total_steps
        if not total:
            _debug.info("progress: an event carried no usable step total")
            return None
        if isinstance(step, bool) or not isinstance(step, int) or step < 0:
            _debug.info("progress: an event carried no usable step")
            return None
        if step > total:
            # More steps than the run has. Not clamped: the two numbers
            # disagree and neither can be trusted to stand for the other.
            _debug.info("progress: step %s is past the total %s", step, total)
            return None
        if step <= self._last_step:
            # Already reported, or older than what was. The Trainer publishes
            # once per step and a repeat means a duplicate line, not a second
            # step.
            return None

        percent = round(step / total * 100.0, 2)
        if not math.isfinite(percent) or percent < 0.0 or percent > 100.0:
            _debug.info("progress: %s%% is not a percentage", percent)
            return None

        elapsed = self._elapsed(event)
        if elapsed is None:
            return None

        self._last_step = step
        self._last_elapsed = elapsed
        return RuntimeProgressEvent(
            job_id=self._job_id,
            generation=self._generation,
            progress_percent=percent,
            current_step=step,
            total_steps=total,
            elapsed_sec=elapsed,
            current_epoch=self._epoch(event),
            total_epochs=event.total_epochs or self._total_epochs,
        )

    def _elapsed(self, event: RuntimeEvent) -> int | None:
        """Whole seconds since training began, and never fewer than last time.

        The container's own measurement is preferred: its clock starts when
        the Trainer does, while the Worker's starts when it first reads a
        line -- after the image was pulled and the weights were loaded, which
        on a cold machine is minutes.

        The Backend's column is ``INT``, so this is whole seconds. It is
        floored rather than rounded, so the number never claims a second that
        has not finished.
        """
        reported = event.elapsed_sec
        if reported is None:
            # Either the container did not say, or what it said was not a
            # usable number -- ``RuntimeEvent.elapsed_sec`` refuses a negative
            # or non-finite one. Falling back keeps the percentage, which is
            # the part a customer looks at; dropping the whole update over a
            # bad second would be the more expensive mistake.
            if "elapsedSec" in event.payload:
                _debug.info("progress: the container's elapsed time was unusable")
            if self._started_at is None:
                return None
            reported = self._monotonic() - self._started_at
        if not math.isfinite(reported) or reported < 0:
            _debug.info("progress: %s is not an elapsed time", reported)
            return None
        seconds = int(reported)
        # A clock that appears to have gone backwards is reported as standing
        # still rather than dropped: the step behind it is real progress, and
        # a stalled second is a smaller lie than losing the update.
        return max(seconds, self._last_elapsed)

    def _epoch(self, event: RuntimeEvent) -> int | None:
        """The epoch the container named, if it named one and it makes sense.

        Not derived from anything. If the container did not say, this is
        absent, and the Backend stores a null -- which is the truth.
        """
        current = event.current_epoch
        if current is None or current < 1:
            return None
        total = event.total_epochs or self._total_epochs
        if total and current > total:
            _debug.info("progress: epoch %s is past the total %s", current, total)
            return None
        return current

    def close(self) -> None:
        """This run is over. Anything still in the pipe is late."""
        self._closed = True
        self._running = False


class ProgressChannel:
    """The one slot the latest progress lives in, shared across two threads.

    Deliberately not a queue. A queue of progress fills up while a connection
    is down and then delivers a history nobody wants; this holds the newest
    and forgets the rest, so what goes out after a reconnection is where the
    run actually is.

    Reading takes the value. A second read with nothing new returns ``None``,
    so the connection loop -- which turns several times a second -- sends a
    message only when there is something to say.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: RuntimeProgressEvent | None = None
        self._taken = True
        self._generation = 0

    def next_generation(self) -> int:
        """Claim a number for a training run about to start.

        Also drops whatever the previous run left behind: it describes a
        container that is no longer running, and sending it now would show a
        customer the last run's percentage for their new Job.
        """
        with self._lock:
            self._generation += 1
            self._latest = None
            self._taken = True
            return self._generation

    def publish(self, event: RuntimeProgressEvent) -> None:
        """Make this the latest. Called from the runtime thread."""
        with self._lock:
            if event.generation < self._generation:
                # A line from a container that has already been replaced.
                _debug.info("progress: an event from run %s arrived late",
                            event.generation)
                return
            self._latest = event
            self._taken = False

    def take(self) -> RuntimeProgressEvent | None:
        """The latest, once. ``None`` when nothing new has happened."""
        with self._lock:
            if self._taken or self._latest is None:
                return None
            self._taken = True
            return self._latest

    def peek(self) -> RuntimeProgressEvent | None:
        """The latest, without claiming it. For tests and for logging."""
        with self._lock:
            return self._latest

    def resend_on_next_take(self) -> None:
        """Offer the latest again, once.

        Called after a reconnection. The Backend's view of this Job stopped
        updating when the socket went; one message puts it where the run
        actually is. The steps that happened in between are not replayed --
        they were superseded while the line was down, which is the whole
        reason this holds one value.
        """
        with self._lock:
            if self._latest is not None:
                self._taken = False

    def clear(self) -> None:
        """Forget it. The run this described is over."""
        with self._lock:
            self._latest = None
            self._taken = True


def _positive(value: Any) -> int | None:
    """A whole number above zero, or nothing. ``True`` is not a number here."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value
