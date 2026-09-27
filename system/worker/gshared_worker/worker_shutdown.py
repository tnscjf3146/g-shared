"""Stopping the Worker because the supplier asked, without walking out on a Job.

:class:`~gshared_worker.worker_connection.InterruptibleStop` is the Worker's one
stop signal: the connection loop checks it every turn, every wait is cut short
by it, and ``stay_connected`` closes the socket and the acceptance when it is
set. This is that signal, with the one thing a person closing a window needs
that Ctrl+C never offered: **waiting for the Job in hand to finish first.**

Why waiting is the safe default rather than stopping at once:

* The training container runs attached to this process (``docker run --rm``).
  A process that exits mid-run leaves that container training for nobody.
* The Job is ASSIGNED to this machine on chain. Only two endings exist for it
  in this Worker's contract with the Backend -- a terminal message after
  settlement, or a cancellation the **customer** asked for. A Worker that
  gave the deposit back on its own would be inventing a third.

So "after the Job" means exactly what the connection loop already does: the
Job settles, its terminal message goes out, the slot is released -- and at
that moment this signal reads as set and the loop stops the way it always
has. No new message, no new transaction, no kill.

Stopping *now* is still available, deliberately behind a second question in
the window. It is the same stop Ctrl+C has always been.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from .logging_setup import LOGGER_NAME
from .worker_connection import InterruptibleStop

_debug = logging.getLogger(LOGGER_NAME)


class WorkerShutdown(InterruptibleStop):
    """A stop signal that can also mean "once the Job in hand is finished"."""

    def __init__(self) -> None:
        super().__init__()
        self._after_work = threading.Event()
        self._busy: Callable[[], bool] = lambda: False

    # -- asking -----------------------------------------------------------

    def request_now(self) -> None:
        """Stop at the next checkpoint, Job or no Job. Same as Ctrl+C."""
        _debug.debug("shutdown: requested now")
        self.set()

    def request_after_work(self) -> None:
        """Stop once nothing is held. Immediately, when nothing is."""
        _debug.debug("shutdown: requested after the job in hand")
        self._after_work.set()

    @property
    def requested(self) -> bool:
        return super().is_set() or self._after_work.is_set()

    @property
    def requested_now(self) -> bool:
        return super().is_set()

    # -- the connection loop's side ---------------------------------------

    def watch_work(self, busy: Callable[[], bool]) -> None:
        """Called by the connection stage with a way to ask whether a Job is held."""
        self._busy = busy

    def accepting_work(self) -> bool:
        """Whether a new Job may still be taken. Not once a stop was asked for."""
        return not self.requested

    def is_set(self) -> bool:
        if super().is_set():
            return True
        if not self._after_work.is_set():
            return False
        try:
            return not self._busy()
        except Exception:
            # Not knowing is not the same as idle. Keep going.
            _debug.debug("shutdown: could not tell whether a job is held",
                         exc_info=True)
            return False

    def wait(self, timeout: float | None = None) -> bool:
        # In slices, re-reading is_set each time: "after the Job" becomes true
        # without anybody calling set(), so the event alone cannot say so.
        remaining = timeout
        while remaining is None or remaining > 0:
            if self.is_set():
                return True
            piece = self.SLICE_SECONDS if remaining is None else min(
                self.SLICE_SECONDS, remaining)
            if self._event.wait(piece):
                return True
            if remaining is not None:
                remaining -= piece
        return self.is_set()
