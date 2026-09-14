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
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from .config import WorkerSettings, data_directory, resolve_settings
from .environment_check import check_environment
from .errors import WorkerAlreadyRunningError, WorkerConfigurationError, WorkerError
from .exit_codes import WorkerExitCode
from .instance_guard import WorkerInstanceGuard, default_lock_directory
from .auth_check import authenticate_worker
from .logging_setup import LOGGER_NAME, stamp_the_build, start_debug_log
from .runtime_check import ensure_runtime
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
) -> int:
    """Everything, in order. Returns the code the launcher reads."""
    directory = directory or data_directory()
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
    say("설정을 읽는 중...")
    try:
        settings = resolve_settings(env=env, directory=directory)
    except WorkerConfigurationError as error:
        # Anticipated: a machine nobody has configured, or a file with a typo
        # in it. Both are for the supplier to fix, and neither is a fault.
        _debug.info("configuration refused: %s", error)
        say("")
        say("이 설정으로는 G-shared를 시작할 수 없습니다.")
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
        _debug.info("identity refused: %s", error)
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
        _debug.info("%s", WorkerExitCode.ALREADY_RUNNING.issue_code)
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
        say("이 PC에서 G-shared가 이미 실행 중입니다.")
        return int(WorkerExitCode.ALREADY_RUNNING)
    except WorkerError as error:
        # Anticipated and already explained by whoever raised it.
        _debug.info("expected failure: %s", error)
        say("")
        say(f"G-shared가 멈췄습니다: {error}")
        return int(WorkerExitCode.EXPECTED_FAILURE)
    _debug.info("worker stopped with exit code %s", code)
    return code


def start_the_machine(settings: WorkerSettings, say: Callable[..., None]) -> int:
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
    code = check_environment(settings, say)
    if code != WorkerExitCode.OK:
        return code
    code = ensure_runtime(settings, say)
    if code != WorkerExitCode.OK:
        return code
    code = authenticate_worker(settings, say)
    if code != WorkerExitCode.OK:
        return code
    return connect_to_backend(settings, say)


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
        say("Enter 키를 누르면 창이 닫힙니다.")
        (wait or input)()
    except Exception:
        # Being unable to hold the window open is not worth failing over; the
        # exit code and the log still say what happened.
        _debug.info("could not hold the window open", exc_info=True)
