"""One program the supplier runs, and what happens in it, in order.

Start the log, read the configuration, take the single-instance identity, run
the Worker, give the identity back. This file owns that order, what the supplier
is told, and which exit code the launcher gets -- and deliberately owns nothing
else.

The stage that follows is ``run_worker``. Today it is two steps -- diagnosing
the machine, then bringing the GPU runtime up inside our own WSL distribution.
Enrolling the machine and running the Worker client arrive with their own
branches and take the same place.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from .config import WorkerSettings, resolve_settings, worker_directory
from .environment_check import check_environment
from .errors import WorkerAlreadyRunningError, WorkerConfigurationError, WorkerError
from .exit_codes import WorkerExitCode
from .instance_guard import WorkerInstanceGuard, default_lock_directory
from .logging_setup import LOGGER_NAME, stamp_the_build, start_debug_log
from .runtime_check import ensure_runtime

_debug = logging.getLogger(LOGGER_NAME)

#: What a stage is: it is given the settings and the one way to speak to the
#: supplier, and it returns the code the launcher reads.
RunWorker = Callable[[WorkerSettings, Callable[..., None]], int]


def run(
    *,
    out: Callable[..., None] | None = None,
    env: Mapping[str, str] | None = None,
    directory: Path | None = None,
    run_worker: RunWorker | None = None,
    guard_factory: Any = WorkerInstanceGuard,
) -> int:
    """Everything, in order. Returns the code the launcher reads."""
    directory = directory or worker_directory()
    log_path = start_debug_log(directory)
    stamp_the_build()
    say = out or print
    run_worker = run_worker or start_the_machine

    try:
        exit_code = _start(
            say,
            os.environ if env is None else env,
            directory,
            run_worker,
            guard_factory,
            # An injected environment must also decide where the identity
            # lives, or a test still writes into the real user profile.
            lock_directory=None if env is None else default_lock_directory(env),
        )
    except KeyboardInterrupt:
        # Somebody decided to stop it. That is not a fault, and a traceback for
        # it reads as a crash they caused.
        _debug.info("stopped from the keyboard")
        say("")
        say("Stopped.")
        exit_code = int(WorkerExitCode.OK)
    except Exception:
        # The last place anything can be written down. Beyond here the console
        # is destroyed with the process and the log is all that is left.
        _debug.exception("unexpected fault")
        say("")
        say("G-shared stopped because of a fault it did not expect.")
        say(f"  What went wrong is written to {log_path}.")
        say("  Sending that file is enough for someone to tell you why.")
        exit_code = int(WorkerExitCode.UNEXPECTED_FAULT)

    _pause_if_the_window_will_close(exit_code, out=say)
    return exit_code


def _start(
    say: Callable[..., None],
    env: Mapping[str, str],
    directory: Path,
    run_worker: RunWorker,
    guard_factory: Any,
    *,
    lock_directory: Path | None,
) -> int:
    """The run itself, with the failures that were anticipated named."""
    say("")
    say("Reading configuration...")
    try:
        settings = resolve_settings(env=env, directory=directory)
    except WorkerConfigurationError as error:
        # Anticipated: a machine nobody has configured, or a file with a typo
        # in it. Both are for the supplier to fix, and neither is a fault.
        _debug.info("configuration refused: %s", error)
        say("")
        say("G-shared cannot start with this configuration.")
        say(f"  {error}")
        return int(WorkerExitCode.EXPECTED_FAILURE)

    _debug.info(
        "configuration: worker=%s gpu=%s backend=%s from=%s token=%s",
        settings.worker_id,
        settings.gpu_index,
        settings.backend_url,
        settings.config_path or "environment and defaults",
        "present" if settings.worker_token else "absent",
    )
    say(f"  Worker {settings.worker_id} on GPU {settings.gpu_index}")
    say(f"  Backend {settings.backend_url}")

    say("")
    say("Claiming this GPU for one Worker...")
    try:
        guard = guard_factory(
            settings.worker_id, settings.gpu_index, lock_directory=lock_directory
        )
        acquired = guard.acquire()
    except ValueError as error:
        # An unusable identity must not escape as a traceback, nor as anything
        # a launcher would retry.
        _debug.info("identity refused: %s", error)
        say("")
        say(f"G-shared cannot start: {error}")
        return int(WorkerExitCode.EXPECTED_FAILURE)
    except OSError:
        # The message would carry the state directory path; the log has it.
        _debug.exception("the Worker identity could not be acquired")
        say("")
        say("G-shared could not claim this GPU. The debug log says why.")
        return int(WorkerExitCode.EXPECTED_FAILURE)

    if not acquired:
        _debug.info("%s", WorkerExitCode.ALREADY_RUNNING.issue_code)
        say("")
        say("G-shared is already running on this machine.")
        say("  Only one Worker may serve a GPU at a time.")
        say(f"  [{WorkerExitCode.ALREADY_RUNNING.issue_code}]")
        return int(WorkerExitCode.ALREADY_RUNNING)

    say("  This GPU is now served by this Worker only.")
    try:
        return _run_the_worker(say, run_worker, settings)
    finally:
        # Releasing never raises; Windows reclaims the identity anyway when
        # this process ends.
        guard.release()
        _debug.info("released the Worker identity")


def _run_the_worker(
    say: Callable[..., None],
    run_worker: RunWorker,
    settings: WorkerSettings,
) -> int:
    try:
        code = int(run_worker(settings, say))
    except WorkerAlreadyRunningError:
        # Something deeper found the same answer the guard did. Not a fault.
        _debug.info("%s", WorkerExitCode.ALREADY_RUNNING.issue_code)
        say("")
        say("G-shared is already running on this machine.")
        return int(WorkerExitCode.ALREADY_RUNNING)
    except WorkerError as error:
        # Anticipated and already explained by whoever raised it.
        _debug.info("expected failure: %s", error)
        say("")
        say(f"G-shared stopped: {error}")
        return int(WorkerExitCode.EXPECTED_FAILURE)
    _debug.info("worker stopped with exit code %s", code)
    return code


def start_the_machine(settings: WorkerSettings, say: Callable[..., None]) -> int:
    """Diagnose the machine, then bring its GPU runtime up.

    Two stages rather than one because they answer different questions and may
    do different things: the first only looks at a machine nobody may alter,
    the second installs into a distribution G-shared owns. A machine that fails
    the first is never acted on.
    """
    code = check_environment(settings, say)
    if code != WorkerExitCode.OK:
        return code
    return ensure_runtime(settings, say)


def _this_process_owns_the_console() -> bool:
    """True when closing this program closes the window it is printing into.

    Windows gives a double-clicked program a console of its own and destroys it
    the instant the program ends. Started from a terminal somebody already had
    open, that terminal owns itself and has other processes attached.
    """
    import ctypes

    buffer = (ctypes.c_uint32 * 8)()
    attached = ctypes.windll.kernel32.GetConsoleProcessList(buffer, 8)
    return attached == 1


def _pause_if_the_window_will_close(
    exit_code: int,
    *,
    owns_console: Any = None,
    wait: Any = None,
    out: Any = None,
) -> None:
    """Hold a failed run on screen when nothing else will.

    Everything this program says about a configuration it could not use is
    printed and then lost with the window a double-click created.
    """
    if exit_code == 0:
        return
    say = out or print
    try:
        if not (owns_console or _this_process_owns_the_console)():
            return
        say("")
        say("Press Enter to close this window.")
        (wait or input)()
    except Exception:
        # Being unable to hold the window open is not worth failing over; the
        # exit code and the log still say what happened.
        _debug.info("could not hold the window open", exc_info=True)
