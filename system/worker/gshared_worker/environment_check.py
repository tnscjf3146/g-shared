"""The Worker stage that asks whether this machine can run GPU work.

It only looks. Nothing here installs, starts, or configures anything -- the
answer is a report, and acting on it is somebody else's job.

Two audiences, as everywhere else in the Worker: the supplier gets the one or
two lines they can act on, and the debug log gets every prerequisite with the
detail and the remedy, so a machine that failed can be diagnosed from the file
alone.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from .config import WorkerSettings
from .diagnosis import Diagnosis, EnvironmentDiagnosis, Finding
from .exit_codes import WorkerExitCode
from .logging_setup import LOGGER_NAME
from .windows_probe import WindowsHostProbe

_debug = logging.getLogger(LOGGER_NAME)


def check_environment(
    settings: WorkerSettings,
    say: Callable[..., None],
    *,
    diagnosis: Any = None,
) -> int:
    """Diagnose this machine and say whether the Worker may go on."""
    say("")
    say("시스템 환경을 확인하는 중...")
    found = asyncio.run((diagnosis or EnvironmentDiagnosis(WindowsHostProbe())).run())
    _log(found)

    if found.is_ready:
        say("  GPU 환경이 준비되었습니다.")
        return int(WorkerExitCode.OK)

    say("")
    say("이 PC는 아직 GPU 작업을 실행할 준비가 되지 않았습니다.")
    for line in what_is_left(found):
        say(f"  {line}")
    return int(WorkerExitCode.EXPECTED_FAILURE)


def what_is_left(found: Diagnosis) -> list[str]:
    """What the supplier is shown. Everything unresolved goes to the log.

    A prerequisite left untested because an earlier one failed is a consequence,
    not a second thing to fix: showing both turns one problem into four bullet
    points and buries the only line that matters.
    """
    unresolved = [c for c in found.checks if c.finding is not Finding.SATISFIED]
    real = [c for c in unresolved if c.finding is not Finding.UNDETERMINED]

    left: list[str] = []
    # Nothing nameable leaves the untested list as the only thing to say, which
    # beats an empty screen on a machine that has stopped.
    for check in real or unresolved:
        left.append(check.detail)
        if check.remedy:
            left.append(f"  {check.remedy}")
    return left


def _log(found: Diagnosis) -> None:
    """Every prerequisite and what was decided about it, whatever the outcome.

    Written for all of them rather than only the failures: "the driver was fine"
    is exactly what rules out the driver when the next report arrives.
    """
    for check in found.checks:
        _debug.log(logging.DEBUG if found.is_ready else logging.WARNING, 
            "%s [%s]: %s",
            check.prerequisite.value, check.finding.value, check.detail,
        )
        if check.remedy:
            _debug.log(logging.DEBUG if found.is_ready else logging.WARNING, "  remedy: %s", check.remedy)
    _debug.log(logging.DEBUG if found.is_ready else logging.WARNING, "environment ready: %s", found.is_ready)
