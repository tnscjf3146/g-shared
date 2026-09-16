"""One program the supplier runs, and what happens in it, in order.

Start the log, read the configuration, take the single-instance identity, run
the Worker, give the identity back. This file owns that order, what the supplier
is told, and which exit code the launcher gets -- and deliberately owns nothing
else.

The stage that follows is ``run_worker``. Today it is four steps -- diagnosing
the machine, bringing the GPU runtime up inside our own WSL distribution,
establishing whose account the machine belongs to, and telling the Backend the
machine is here. Waiting on that connection for work arrives with its own
branch and takes the same place.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping
from functools import partial
from pathlib import Path
from typing import Any, Callable

from .config import WorkerSettings, data_directory, resolve_settings
from .environment_check import check_environment
from .errors import WorkerAlreadyRunningError, WorkerConfigurationError, WorkerError
from .exit_codes import WorkerExitCode
from .instance_guard import WorkerInstanceGuard, default_lock_directory
from .auth_check import authenticate_worker
from .logging_setup import BUILD, LOGGER_NAME, start_debug_log
from .runtime_check import ensure_runtime
from . import status_events
from .status_events import StageChanged, WorkerStage
from .worker_connection import connect_to_backend

_debug = logging.getLogger(LOGGER_NAME)

#: The single-instance identity of a machine that has no workerId yet.
#:
#: Registration is what issues a workerId, so a first run cannot be guarded by
#: one. Every unregistered run on this account and GPU shares this name instead,
#: which is what stops two first runs from registering the machine twice.
UNREGISTERED_IDENTITY = "unregistered"

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
    stop: Any = None,
    open_sign_in: Callable[[Any], str | None] | None = None,
    show_failure: Callable[[int, list[str]], None] | None = None,
) -> int:
    """Everything, in order. Returns the code the launcher reads.

    ``stop``, ``open_sign_in`` and ``show_failure`` are what the status window
    (:mod:`gshared_worker.status_app`) brings: a stop signal it can set, a way
    to run the sign-in window on the window's own thread, and a way to show a
    failure in the window rather than in a dialog opened from this thread. A
    run from a terminal passes none of them and behaves exactly as before.
    """
    emit = out or print
    #: Everything the supplier was told, kept so a failed run can be shown in a
    #: dialog by the packaged Worker, which has no console to print it to.
    said: list[str] = []
    #: The settings this run used, for the one log line a failed run leaves.
    recorded: list[str] = []

    def say(*parts: object) -> None:
        said.append(" ".join(str(part) for part in parts))
        emit(*parts)

    try:
        directory = directory or data_directory()
    except WorkerConfigurationError as error:
        # Storage is chosen before there is a safe place to write a log. A
        # split-brain wallet must still be an explained, visible refusal rather
        # than an exception on the packaged Worker's background thread.
        say("")
        say("저장된 Worker Operator 정보를 안전하게 선택할 수 없습니다.")
        say(f"  {error}")
        exit_code = int(WorkerExitCode.EXPECTED_FAILURE)
        (show_failure or _show_the_failure)(exit_code, said)
        return exit_code

    log_path = start_debug_log(directory)

    run_worker = run_worker or _with(
        start_the_machine, stop=stop, open_sign_in=open_sign_in)

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
            recorded=recorded,
        )
    except KeyboardInterrupt:
        # Somebody decided to stop it. That is not a fault, and a traceback for
        # it reads as a crash they caused.
        _debug.debug("stopped from the keyboard")
        say("")
        say("중지했습니다.")
        exit_code = int(WorkerExitCode.OK)
    except Exception:
        # The last place anything can be written down. Beyond here the console
        # is destroyed with the process and the log is all that is left.
        _debug.exception("unexpected fault")
        say("")
        say("예상하지 못한 오류로 G-shared가 멈췄습니다.")
        say(f"  오류 내용은 {log_path} 에 기록했습니다.")
        say("  이 파일을 보내 주시면 원인을 확인할 수 있습니다.")
        exit_code = int(WorkerExitCode.UNEXPECTED_FAULT)

    if exit_code != 0:
        # The one line a report needs when nothing else was worth writing:
        # which build, which settings, and how it ended.
        _debug.warning(
            "stopped with exit code %s (build %s; %s)", exit_code, BUILD,
            recorded[0] if recorded else "configuration not read")
        (show_failure or _show_the_failure)(exit_code, said)
    return exit_code


def _start(
    say: Callable[..., None],
    env: Mapping[str, str],
    directory: Path,
    run_worker: RunWorker,
    guard_factory: Any,
    *,
    lock_directory: Path | None,
    recorded: list[str] | None = None,
) -> int:
    """The run itself, with the failures that were anticipated named."""
    say("")
    say("설정을 읽는 중...")
    try:
        settings = resolve_settings(env=env, directory=directory)
    except WorkerConfigurationError as error:
        # Anticipated: a machine nobody has configured, or a file with a typo
        # in it. Both are for the supplier to fix, and neither is a fault.
        _debug.warning("configuration refused: %s", error)
        say("")
        say("이 설정으로는 G-shared를 시작할 수 없습니다.")
        say(f"  {error}")
        return int(WorkerExitCode.EXPECTED_FAILURE)

    summary = "worker=%s gpu=%s backend=%s from=%s token=%s" % (
        settings.worker_id,
        settings.gpu_index,
        settings.backend_url,
        settings.config_path or "environment and defaults",
        "present" if settings.worker_token else "absent",
    )
    _debug.debug("configuration: %s", summary)
    if recorded is not None:
        recorded.append(summary)
    if settings.worker_id is None:
        say(f"  이 PC는 아직 등록되지 않았습니다 (GPU {settings.gpu_index}).")
        say("  점검이 끝나면 로그인 창이 열립니다.")
    else:
        say(f"  Worker {settings.worker_id} (GPU {settings.gpu_index})")
    say(f"  서버 {settings.backend_url}")

    say("")
    say("이 GPU를 이 Worker 전용으로 잡는 중...")
    try:
        guard = guard_factory(
            settings.worker_id or UNREGISTERED_IDENTITY,
            settings.gpu_index,
            lock_directory=lock_directory,
        )
        acquired = guard.acquire()
    except ValueError as error:
        # An unusable identity must not escape as a traceback, nor as anything
        # a launcher would retry.
        _debug.warning("identity refused: %s", error)
        say("")
        say(f"G-shared를 시작할 수 없습니다: {error}")
        return int(WorkerExitCode.EXPECTED_FAILURE)
    except OSError:
        # The message would carry the state directory path; the log has it.
        _debug.exception("the Worker identity could not be acquired")
        say("")
        say("이 GPU를 잡지 못했습니다. 원인은 디버그 로그에 있습니다.")
        return int(WorkerExitCode.EXPECTED_FAILURE)

    if not acquired:
        _debug.warning("%s", WorkerExitCode.ALREADY_RUNNING.issue_code)
        say("")
        say("이 PC에서 G-shared가 이미 실행 중입니다.")
        say("  GPU 하나에는 Worker 하나만 실행할 수 있습니다.")
        say(f"  [{WorkerExitCode.ALREADY_RUNNING.issue_code}]")
        return int(WorkerExitCode.ALREADY_RUNNING)

    say("  이제 이 GPU는 이 Worker만 사용합니다.")
    try:
        return _run_the_worker(say, run_worker, settings)
    finally:
        # Releasing never raises; Windows reclaims the identity anyway when
        # this process ends.
        guard.release()
        _debug.debug("released the Worker identity")


def _run_the_worker(
    say: Callable[..., None],
    run_worker: RunWorker,
    settings: WorkerSettings,
) -> int:
    try:
        code = int(run_worker(settings, say))
    except WorkerAlreadyRunningError:
        # Something deeper found the same answer the guard did. Not a fault.
        _debug.warning("%s", WorkerExitCode.ALREADY_RUNNING.issue_code)
        say("")
        say("이 PC에서 G-shared가 이미 실행 중입니다.")
        return int(WorkerExitCode.ALREADY_RUNNING)
    except WorkerError as error:
        # Anticipated and already explained by whoever raised it.
        _debug.warning("expected failure: %s", error)
        say("")
        say(f"G-shared가 멈췄습니다: {error}")
        return int(WorkerExitCode.EXPECTED_FAILURE)
    _debug.debug("worker stopped with exit code %s", code)
    return code


def start_the_machine(
    settings: WorkerSettings,
    say: Callable[..., None],
    *,
    stop: Any = None,
    open_sign_in: Callable[[Any], str | None] | None = None,
) -> int:
    """Diagnose the machine, bring its GPU runtime up, say whose it is, connect.

    Separate stages because they answer different questions and may do
    different things: the first only looks at a machine nobody may alter, the
    second installs into a distribution G-shared owns, the third asks a person
    to sign in, the fourth speaks to the Backend as the machine. Each one is
    reached only when the one before it passed -- there is no point attaching a
    machine to an account before knowing it can do the work, no point
    installing into a machine that failed diagnosis, and no point offering a
    machine to the Backend before the Backend has said it accepts it.

    That last one is the reason the connection is a stage of its own rather
    than the tail of the one before it: the only credential it may use is the
    machine's own token, and whether that token is still accepted is exactly
    what the stage before it establishes.
    """
    stages = (
        (WorkerStage.ENVIRONMENT_CHECK, check_environment, {}),
        (WorkerStage.RUNTIME_PREPARING, ensure_runtime, {}),
        (WorkerStage.AUTHENTICATING, authenticate_worker,
         {"open_sign_in": open_sign_in}),
    )
    for stage, run_stage, extra in stages:
        # Between stages, never inside one: an installation or a benchmark
        # left half done is worse than one finished a minute late.
        if _asked_to_stop(stop, say):
            return int(WorkerExitCode.OK)
        status_events.publish(StageChanged(stage))
        code = _with(run_stage, **extra)(settings, say)
        if code != WorkerExitCode.OK:
            return code
    if _asked_to_stop(stop, say):
        return int(WorkerExitCode.OK)
    status_events.publish(StageChanged(WorkerStage.CONNECTING))
    return _with(connect_to_backend, stop=stop)(settings, say)


def _with(stage: Callable[..., int], **extra: Any) -> Callable[..., int]:
    """The stage, called with ``(settings, say)`` plus whatever was really given.

    Nothing given means the stage is called exactly as it always was, which is
    what every stage double in the tests is written against.
    """
    given = {name: value for name, value in extra.items() if value is not None}
    return partial(stage, **given) if given else stage


def _asked_to_stop(stop: Any, say: Callable[..., None]) -> bool:
    """Whether the supplier asked G-shared to stop before the next stage."""
    if stop is None or not stop.is_set():
        return False
    _debug.debug("stopped between stages on request")
    say("")
    say("종료 요청에 따라 G-shared를 멈췄습니다.")
    return True


#: How many of the last lines a failure dialog shows. The end of a run is
#: where it says what went wrong and what to do about it.
_DIALOG_LINES = 25


def _is_windowed() -> bool:
    """True for the packaged G-shared.exe, which has no console to print to."""
    return bool(getattr(sys, "frozen", False)) and sys.stdout is None


def _show_the_failure(
    exit_code: int,
    said: list[str],
    *,
    windowed: bool | None = None,
    show: Callable[[str], None] | None = None,
) -> None:
    """Put the end of a failed run in front of the supplier.

    The packaged Worker has no console, so what it says about a configuration
    it could not use, a machine that is not ready, or a copy that is already
    running would otherwise vanish with nothing on screen. Run from a terminal
    those lines are already there, and nothing more is shown.
    """
    if exit_code == 0:
        return
    if not (_is_windowed() if windowed is None else windowed):
        return
    lines = [line.rstrip() for line in said if line.strip()]
    text = chr(10).join(lines[-_DIALOG_LINES:]) or "G-shared가 멈췄습니다."
    try:
        (show or _error_dialog)(text)
    except Exception:
        # Not being able to show it is no reason to fail; the exit code and
        # the log still say what happened.
        _debug.warning("the failure could not be shown", exc_info=True)


def _error_dialog(text: str) -> None:
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()
    try:
        messagebox.showerror("G-shared", text, parent=root)
    finally:
        root.destroy()
