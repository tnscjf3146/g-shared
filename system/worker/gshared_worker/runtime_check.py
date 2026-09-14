"""The Worker stage that makes this machine able to run GPU containers.

The stage before it only looked. This one may act -- but only inside the
distribution G-shared owns, and only on what the diagnosis said was missing.

Two audiences, as everywhere else in the Worker: the supplier gets the lines
they can act on, and the debug log gets every prerequisite, every action and
every outcome, so a machine that failed can be diagnosed from the file alone.

RUNTIME READY means all four prerequisites came back SATISFIED **after** the
last action, including a container that really saw the GPU. A step that reported
success is never enough on its own.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from .config import WSL_DISTRO_NAME, WorkerSettings
from .container_runtime import ContainerGpuProbe
from .diagnosis import Finding
from .exit_codes import WorkerExitCode
from .logging_setup import LOGGER_NAME
from .runtime_diagnosis import (
    RuntimeDiagnosis,
    RuntimeDiagnosisRunner,
    RuntimePrerequisite,
)
from .runtime_setup import GsharedProvisioner, Progress, RuntimeSetup, Setup
from .wsl_runtime import GsharedRuntimeProbe, WslCommandRunner, WslDistroProbe

_debug = logging.getLogger(LOGGER_NAME)

#: What each step is, said without naming anything a supplier was promised they
#: would never have to manage.
_IN_PLAIN_WORDS = {
    RuntimePrerequisite.GSHARED_DISTRO:
        "전용 Linux 환경을 설치하는 중... (약 500 MB)",
    RuntimePrerequisite.DOCKER_ENGINE:
        "GPU 작업을 실행할 프로그램을 설치하는 중... (약 500 MB)",
    RuntimePrerequisite.CONTAINER_TOOLKIT:
        "그 프로그램이 GPU를 쓸 수 있게 설정하는 중... (약 400 MB)",
    RuntimePrerequisite.CONTAINER_GPU:
        "컨테이너에서 GPU가 보이는지 확인하는 중...",
}


def build_runtime_setup(announce: Any = None) -> RuntimeSetup:
    """The real thing: our distribution, its engine, its toolkit, its GPU."""
    inside = WslCommandRunner()
    diagnosis = RuntimeDiagnosisRunner(
        WslDistroProbe(), GsharedRuntimeProbe(inside), ContainerGpuProbe(inside)
    )
    return RuntimeSetup(diagnosis, GsharedProvisioner(), announce=announce)


def ensure_runtime(
    settings: WorkerSettings,
    say: Callable[..., None],
    *,
    setup: RuntimeSetup | None = None,
) -> int:
    """Bring the GPU runtime up, or say plainly what is in the way."""
    say("")
    say("GPU 실행 환경을 준비하는 중...")
    say(f"  모든 작업은 {WSL_DISTRO_NAME} WSL 배포판 안에서 실행됩니다.")

    prepared = asyncio.run(
        (setup or build_runtime_setup(announce=_announce(say))).prepare()
    )
    _log(prepared)

    if prepared.is_ready:
        say("")
        say("GPU 실행 환경 준비 완료.")
        for line in _what_is_working(prepared.diagnosis):
            say(f"  {line}")
        return int(WorkerExitCode.OK)

    say("")
    say("이 PC의 GPU 실행 환경이 준비되지 않았습니다.")
    if prepared.progress is Progress.NEEDS_A_RESTART:
        # Said first and on its own: everything below it is a consequence of
        # work Windows has not finished, and reading those as separate problems
        # sends somebody looking for faults a restart will clear.
        say("  PC를 다시 시작한 뒤 G-shared를 다시 실행하세요.")
    for line in what_is_left(prepared):
        say(f"  {line}")
    return int(WorkerExitCode.EXPECTED_FAILURE)


def what_is_left(prepared: Setup) -> list[str]:
    """What the supplier is shown. Everything unresolved goes to the log.

    A prerequisite left untested because an earlier one failed is a consequence,
    not a second thing to fix: showing both turns one problem into four bullet
    points and buries the only line that matters.
    """
    unresolved = [
        c for c in prepared.diagnosis.checks if c.finding is not Finding.SATISFIED
    ]
    real = [c for c in unresolved if c.finding is not Finding.UNDETERMINED]

    #: What an action said when it was tried and refused. The finding restates
    #: the problem; this says why it could not be fixed.
    refused = {
        attempt.prerequisite: attempt.detail
        for attempt in prepared.attempts if not attempt.succeeded
    }

    left: list[str] = []
    # Nothing nameable leaves the untested list as the only thing to say, which
    # beats an empty screen on a machine that has stopped.
    for check in real or unresolved:
        left.append(check.detail)
        if check.remedy:
            left.append(f"  {check.remedy}")
        if check.prerequisite in refused:
            left.append(f"  시도했지만 실패했습니다: {refused[check.prerequisite]}")
    return left


def _what_is_working(found: RuntimeDiagnosis) -> list[str]:
    """The one line worth showing on success: what the GPU actually said."""
    gpu = found.find(RuntimePrerequisite.CONTAINER_GPU)
    return [gpu.detail]


def _announce(say: Callable[..., None]) -> Callable[..., None]:
    """Name each step before it starts.

    Preparing a fresh machine downloads well over a gigabyte, and a person
    watching a single unchanging line has no way to tell that from a hang.
    """
    warned = False

    def announce(check) -> None:
        nonlocal warned
        if not warned:
            warned = True
            say("")
            say("  처음 한 번만 필요한 설치를 진행합니다.")
            say("  약 1.5 GB를 내려받아 몇 분 걸리고,")
            say("  인터넷이 느리면 더 걸립니다. 창을 닫지 말고 기다려 주세요.")
        say("  " + _IN_PLAIN_WORDS.get(
            check.prerequisite, f"{check.prerequisite.value} 설치 중..."))

    return announce


def _log(prepared: Setup) -> None:
    """Every action taken and every prerequisite, whatever the outcome.

    Written for the successes too: "the toolkit was already configured" is
    exactly what rules the toolkit out when the next report arrives.
    """
    for attempt in prepared.attempts:
        _debug.log(logging.DEBUG if prepared.diagnosis.is_ready else logging.WARNING, 
            "%s %s: %s -- %s",
            attempt.action.value, attempt.prerequisite.value,
            "ok" if attempt.succeeded else "failed", attempt.detail,
        )
    for check in prepared.diagnosis.checks:
        _debug.log(logging.DEBUG if prepared.diagnosis.is_ready else logging.WARNING, 
            "%s [%s]: %s",
            check.prerequisite.value, check.finding.value, check.detail,
        )
        if check.remedy:
            _debug.log(logging.DEBUG if prepared.diagnosis.is_ready else logging.WARNING, "  remedy: %s", check.remedy)
    _debug.log(logging.DEBUG if prepared.diagnosis.is_ready else logging.WARNING, 
        "runtime %s (ready=%s)", prepared.progress.value, prepared.diagnosis.is_ready
    )
