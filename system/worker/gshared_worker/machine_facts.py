"""What this machine is, in the words the Worker API asks for.

Registering a machine means telling the Backend what it is registering: a name
for a person to recognise it by, the OS it runs, and the card it is offering.
Every value here is read from the machine itself. **Nothing is invented**: when
the GPU cannot be described, this answers None and the caller stops, because a
Worker registered with a made-up card is a Worker that gets sent work it cannot
do.

The benchmark figure is deliberately *not* here. It is not a property of the
machine that can be read off it -- it has to be measured, and measuring it is
the next branch's work. See :mod:`gshared_worker.auth_check` for the seam it
arrives through.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
from dataclasses import dataclass

from .command_runner import AsyncSubprocessRunner, CommandRunner
from .container_runtime import parse_gpu_report
from .errors import WorkerError
from .logging_setup import LOGGER_NAME

_debug = logging.getLogger(LOGGER_NAME)

_PROBE_TIMEOUT_SECONDS = 20
_PROBE_OUTPUT_LIMIT = 64 * 1024


@dataclass(frozen=True)
class MachineFacts:
    """The machine, as the registration request describes it."""

    device_name: str
    os_version: str
    gpu_model: str
    vram_mb: int
    driver_version: str


def describe_machine(runner: CommandRunner | None = None) -> MachineFacts | None:
    """This machine, or None when its GPU could not be described.

    The GPU is asked on the Windows host rather than inside a container: the
    runtime stage has already proved a container can reach the card, and asking
    again through Docker would cost seconds to learn what the driver's own tool
    answers immediately.
    """
    name = platform.node() or ""
    if not name:
        import socket

        name = socket.gethostname() or ""
    if not name:
        _debug.warning("machine facts: this machine has no hostname")
        return None

    report = asyncio.run(_gpu(runner or AsyncSubprocessRunner()))
    if report is None or report.vram_mib is None:
        # A card whose memory nvidia-smi would not state cannot be offered with
        # a vramMb the Backend can schedule against.
        _debug.warning("machine facts: the GPU could not be described")
        return None

    facts = MachineFacts(
        device_name=name,
        os_version=_os_version(),
        gpu_model=report.model,
        vram_mb=report.vram_mib,
        driver_version=report.driver_version,
    )
    _debug.debug(
        "machine facts: %s / %s / %s (%s MB, driver %s)",
        facts.device_name, facts.os_version, facts.gpu_model,
        facts.vram_mb, facts.driver_version,
    )
    return facts


def _os_version() -> str:
    """The OS version as this machine states it."""
    version = platform.version() or platform.release() or ""
    if os.name == "nt":
        return f"Windows {version}".strip()
    return version or platform.system()


async def _gpu(runner: CommandRunner):
    """The first card nvidia-smi names, or None when it named none."""
    try:
        result = await runner.run(
            [
                "nvidia-smi", "--query-gpu=name,driver_version,memory.total",
                "--format=csv,noheader,nounits",
            ],
            timeout=_PROBE_TIMEOUT_SECONDS,
            max_output_bytes=_PROBE_OUTPUT_LIMIT,
        )
    except (FileNotFoundError, WorkerError):
        # No driver tool on this machine. The diagnosis stage already says so
        # in words; here it simply means there is nothing to register.
        return None
    if result.exit_code != 0:
        return None
    return parse_gpu_report(result.stdout)
