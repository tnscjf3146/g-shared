"""G-shared.exe with a window: the window on this thread, the Worker on another.

The packaged Worker has no console. Before this, a machine that started, passed
every check and connected showed nothing at all, and the only way to stop it
was the Task Manager. This module is what the executable runs instead of
:func:`gshared_worker.main.run` directly:

::

    main thread                         gshared-worker thread
    -----------                         ---------------------
    status window opens
    GPU is described (background)
    Worker thread starts   ---------->  main.run(...)  -- unchanged stages
    Tk loop drains the queue  <-------  status_events.publish(...)
    sign-in window runs here  <------>  waits on SignInRequest.done
    [종료] / X  -> WorkerShutdown  ---> loop stops at its next checkpoint
    window closes          <----------  WorkerFinished

**The window never waits on the Worker and the Worker never touches the window.**
Values cross in one direction through a queue; the one question that has to
come back -- the access token from the sign-in window -- crosses on an Event.

A run from a terminal does not come here: :func:`launch` hands it straight to
:func:`gshared_worker.main.run`, exactly as before.
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Any, Callable

from . import main as _main
from . import status_events
from .exit_codes import WorkerExitCode
from .gui.status_presenter import (
    FailureToShow,
    ShutdownAction,
    SignInRequest,
    StatusPresenter,
    WorkerFinished,
)
from .logging_setup import LOGGER_NAME
from .status_events import GpuDescribed, Said, StageChanged, StatusQueue, WorkerStage
from .worker_shutdown import WorkerShutdown

_debug = logging.getLogger(LOGGER_NAME)

#: How long a finished, stopped Worker's window stays up before it closes, so
#: the last line can be seen rather than flashed.
CLOSE_DELAY_MS = 600

#: How often a Worker thread waiting on the sign-in window checks whether the
#: window it is waiting on has gone.
SIGN_IN_POLL_SECONDS = 0.5


def launch() -> int:
    """What the executable runs: the window, whenever this is the packaged Worker.

    Decided by being frozen, not by whether standard output exists. G-shared.exe
    is built without a console, but a launcher, a shortcut or a scheduled task
    can still hand it inherited handles -- and a Worker that decided on those
    would run with no window and no way to stop it, which is the problem this
    module exists to end. Run from source it belongs to the terminal, as before.
    """
    if not getattr(sys, "frozen", False):
        return _main.run()
    return StatusApp().run()


class StatusApp:
    """Owns the two threads and the one conversation between them."""

    def __init__(
        self,
        *,
        run: Callable[..., int] | None = None,
        make_window: Callable[..., Any] | None = None,
        describe: Callable[[], Any] | None = None,
        shutdown: WorkerShutdown | None = None,
    ) -> None:
        self._run = run or _main.run
        self._make_window = make_window or _real_window
        self._describe = describe or _describe_this_gpu
        self.shutdown = shutdown or WorkerShutdown()
        self.feed = StatusQueue()
        self.presenter = StatusPresenter()
        self.exit_code: int | None = None
        self.window: Any = None
        self._pending_sign_in: SignInRequest | None = None
        self._window_gone = threading.Event()

    # -- the main thread ------------------------------------------------------

    def run(self) -> int:
        previous = status_events.install(self.feed)
        worker = threading.Thread(target=self._work, name="gshared-worker")
        try:
            # The window first, drawn before anything slow begins, so starting
            # G-shared is never a long wait in front of nothing.
            self.window = self._make_window(
                self.presenter, self.feed, self.act, self.on_event)
            status_events.publish(StageChanged(WorkerStage.STARTING))
            threading.Thread(target=self._look_at_the_gpu, name="gshared-gpu",
                             daemon=True).start()
            worker.start()
            self.window.run()
        finally:
            self._window_gone.set()
            if worker.is_alive():
                # The window went without the Worker having finished -- the
                # Windows session is ending, most likely. Stop the ordinary way
                # and wait for it: the instance lock and the socket are released
                # on the Worker's own thread.
                _debug.warning("status: the window closed while the worker was running")
                self.shutdown.request_now()
                worker.join()
            status_events.install(previous)
        return int(self.exit_code if self.exit_code is not None
                   else WorkerExitCode.OK)

    def act(self, action: ShutdownAction) -> None:
        """What the presenter decided closing means. On the window's thread."""
        if action is ShutdownAction.STOP_NOW:
            self.shutdown.request_now()
            self._close_the_sign_in_window()
        elif action is ShutdownAction.STOP_AFTER_WORK:
            self.shutdown.request_after_work()
        elif action is ShutdownAction.CLOSE:
            self.window.close()

    def on_event(self, event: Any) -> None:
        """The few events the window acts on, not only draws. Window's thread."""
        if isinstance(event, SignInRequest):
            # Later rather than now, so the queue pump schedules its next turn
            # first and keeps drawing while the sign-in window is open.
            self.window.later(0, lambda: self._open_sign_in(event))
        elif isinstance(event, FailureToShow):
            self.window.later(0, lambda: self.window.show_error(
                self.presenter.failure or ""))
        elif isinstance(event, WorkerFinished) and self.shutdown.requested:
            self.window.later(CLOSE_DELAY_MS, self.window.close)

    def _open_sign_in(self, request: SignInRequest) -> None:
        if self.shutdown.requested:
            request.done.set()
            return
        self._pending_sign_in = request
        try:
            request.result = request.window.ask_to_sign_in()
        except Exception:
            _debug.exception("status: the sign-in window failed")
            request.result = None
        finally:
            self._pending_sign_in = None
            request.done.set()

    def _close_the_sign_in_window(self) -> None:
        pending = self._pending_sign_in
        if pending is not None:
            pending.window.close()

    # -- the Worker thread ----------------------------------------------------

    def _work(self) -> None:
        code = int(WorkerExitCode.UNEXPECTED_FAULT)
        try:
            code = int(self._run(
                out=self._say,
                stop=self.shutdown,
                open_sign_in=self._sign_in,
                show_failure=self._failure))
        finally:
            self.exit_code = code
            status_events.publish(WorkerFinished(code))

    def _say(self, *parts: object) -> None:
        """What ``say`` does from here: print where there is a console, and publish."""
        if sys.stdout is not None:
            print(*parts)
        status_events.publish(Said(" ".join(str(part) for part in parts)))

    def _failure(self, exit_code: int, said: list[str]) -> None:
        """The failure dialog's text, shown by the window instead of this thread."""
        if self.shutdown.requested:
            # The person asked it to stop; how the last stage ended is not an
            # error to put in front of them.
            return
        _main._show_the_failure(
            exit_code, said, windowed=True,
            show=lambda text: status_events.publish(FailureToShow(text)))

    def _sign_in(self, window: Any) -> str | None:
        """Run the sign-in window on the window's thread and wait for its answer."""
        if self.shutdown.requested:
            return None
        request = SignInRequest(window)
        status_events.publish(request)
        while not request.done.wait(SIGN_IN_POLL_SECONDS):
            if self._window_gone.is_set():
                return None
        return request.result

    def _look_at_the_gpu(self) -> None:
        try:
            facts = self._describe()
        except Exception:
            _debug.debug("status: the GPU could not be described", exc_info=True)
            return
        if facts is not None:
            status_events.publish(GpuDescribed(facts.gpu_model, facts.vram_mb))


def _real_window(presenter, feed, on_action, on_event):
    from .gui.status_window import StatusWindow

    return StatusWindow(presenter, feed, on_action, on_event).open()


def _describe_this_gpu():
    from .machine_facts import describe_machine

    return describe_machine()
