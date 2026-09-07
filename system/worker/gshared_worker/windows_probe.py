"""Reading this Windows machine for :mod:`gshared_worker.diagnosis`.

Every method answers with a value, never an exception. A probe that throws when
a tool is absent cannot distinguish "not installed" -- the ordinary case this
whole program exists to report -- from "something went wrong", and the caller
needs that difference to decide between naming a fix and giving up.

Nothing here changes the machine. Every command is a question.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from typing import Any

from .command_runner import AsyncSubprocessRunner, CommandResult, CommandRunner
from .errors import WorkerError

_PROBE_TIMEOUT_SECONDS = 20
_PROBE_OUTPUT_LIMIT = 64 * 1024


@dataclass(frozen=True)
class NvidiaDriver:
    """A driver that answered, and the card it answered for.

    ``gpu_name`` may be None while the driver is perfectly good: it is detail
    for the log and the screen, never part of deciding whether the driver is
    usable. Only the object being None at all means no usable driver.
    """

    version: str
    gpu_name: str | None = None


class WindowsHostProbe:
    """The real machine. See :class:`gshared_worker.diagnosis.HostProbe`."""

    def __init__(
        self,
        runner: CommandRunner | None = None,
        *,
        nvml_module: Any | None = None,
    ) -> None:
        self._runner = runner or AsyncSubprocessRunner()
        self._nvml = nvml_module

    async def windows_build(self) -> int | None:
        """The Windows build number, or None when this is not Windows at all."""
        if os.name != "nt":
            return None
        version = getattr(sys, "getwindowsversion", None)
        if version is None:
            return None
        return int(version().build)

    async def firmware_virtualization_enabled(self) -> bool | None:
        """Whether this machine can virtualize, asked two ways.

        Neither question is sufficient alone. ``VirtualizationFirmwareEnabled``
        describes the firmware switch but flips to False the moment Hyper-V
        claims the CPU extensions -- which is true of every machine already
        running WSL2. ``HypervisorPresent`` is False on a capable machine that
        simply has no hypervisor running yet. Together they settle it: a running
        hypervisor is proof, and only both saying no is a real no.
        """
        result = await self._run([
            "powershell", "-NoProfile", "-NonInteractive", "-Command",
            "\"$((Get-CimInstance Win32_Processor | Select-Object -First 1)"
            ".VirtualizationFirmwareEnabled)|"
            "$((Get-CimInstance Win32_ComputerSystem).HypervisorPresent)\"",
        ])
        if result is None or result.exit_code != 0:
            return None

        firmware, _, hypervisor = result.stdout.strip().partition("|")
        if _boolean(hypervisor) is True:
            # Something is virtualizing right now. Whatever the firmware flag
            # says about itself, the capability is demonstrably there.
            return True

        enabled = _boolean(firmware)
        if enabled is not None:
            return enabled
        # Some processors leave the property empty rather than reporting False.
        # Unreadable is its own answer; guessing here would either send someone
        # into their BIOS for nothing or wave through a machine that cannot run
        # WSL2 at all.
        return None

    async def nvidia_driver(self) -> NvidiaDriver | None:
        """The installed driver, or None if there really is none.

        NVML is asked first because it is what the Worker itself uses later, so
        this is the same question rather than a proxy for it. But NVML lives in
        a Python package, and not being able to load that package says nothing
        about the machine: answering None there would tell someone with a
        perfectly good card to install the driver they already have, and would
        stop every GPU check that follows from being tried at all.

        So a second opinion is asked of the driver's own tool, which ships with
        the driver itself. Only both coming back empty means no driver.
        """
        through_nvml = self._through_nvml()
        if through_nvml is not None:
            return through_nvml
        return await self._through_nvidia_smi()

    def _through_nvml(self) -> NvidiaDriver | None:
        nvml = self._nvml
        if nvml is None:
            try:
                import pynvml
            except ImportError:
                return None
            nvml = pynvml

        try:
            nvml.nvmlInit()
        except Exception:
            return None
        try:
            if nvml.nvmlDeviceGetCount() == 0:
                # A driver with no card behind it cannot run anything.
                return None
            version = _text(nvml.nvmlSystemGetDriverVersion())
            name = self._nvml_gpu_name(nvml)
        except Exception:
            return None
        finally:
            try:
                nvml.nvmlShutdown()
            except Exception:
                pass

        return NvidiaDriver(version, name) if version else None

    @staticmethod
    def _nvml_gpu_name(nvml: Any) -> str | None:
        """The first card's model, if it can be had. Never worth failing over."""
        try:
            return _text(nvml.nvmlDeviceGetName(nvml.nvmlDeviceGetHandleByIndex(0)))
        except Exception:
            return None

    async def _through_nvidia_smi(self) -> NvidiaDriver | None:
        result = await self._run([
            "nvidia-smi", "--query-gpu=driver_version,name", "--format=csv,noheader",
        ])
        if result is None or result.exit_code != 0:
            return None
        # One line per GPU, all reporting the same driver. The first is enough.
        for line in result.stdout.replace("\x00", "").splitlines():
            version, _, name = line.partition(",")
            if version.strip():
                return NvidiaDriver(version.strip(), name.strip() or None)
        return None

    async def wsl_default_version(self) -> int | None:
        """WSL's default version, or None when WSL could not answer at all."""
        result = await self._run(["wsl", "--status"])
        if result is None:
            return None
        # wsl.exe writes UTF-16, which arrives here full of NUL bytes once it
        # has been decoded as UTF-8. Stripping them costs nothing when the
        # output was already sensible.
        text = result.stdout.replace("\x00", "")
        if result.exit_code != 0 and not text.strip():
            return None
        # The digit has to stand alone. Newer wsl.exe prints its own app version
        # ("WSL version: 1.2.5.0"), and a looser match reads that as a default
        # of 1 on a machine that actually defaults to 2.
        match = re.search(r"[:：]\s*([12])(?!\S)", text)
        if match:
            return int(match.group(1))
        if result.exit_code != 0:
            return None
        # WSL answered but said nothing about a default version. Version 2 has
        # been the default since it stopped being worth printing.
        return 2

    async def wsl_can_create_named_distributions(self) -> bool | None:
        """Whether this wsl.exe takes ``--name`` and ``--location`` on install.

        The Worker keeps its work in a distribution of its own, created by name
        and in a location of our choosing. An older wsl.exe refuses both
        options, and the run then ends on a message about a command-line flag,
        which says nothing a supplier can act on. ``None`` means nothing was
        learned, which is not the same as "too old" and must not be treated as
        one.
        """
        result = await self._run(["wsl", "--help"])
        if result is None:
            return None
        # wsl.exe writes UTF-16; decoded as UTF-8 every character is followed by
        # a NUL, so a plain substring test would find nothing at all.
        text = result.stdout.replace("\x00", "")
        if not text.strip():
            return None
        return "--name" in text and "--location" in text

    async def _run(self, args: list[str]) -> CommandResult | None:
        """Run a probe command, reporting an unanswerable one as None."""
        try:
            return await self._runner.run(
                args,
                timeout=_PROBE_TIMEOUT_SECONDS,
                max_output_bytes=_PROBE_OUTPUT_LIMIT,
            )
        except FileNotFoundError:
            return None
        except WorkerError:
            # A missing tool, a timeout and a flood of output all mean the same
            # thing here: this machine did not answer the question.
            return None


def _boolean(text: str) -> bool | None:
    """PowerShell's True/False, or None when it printed neither."""
    answer = text.strip().casefold()
    if answer == "true":
        return True
    if answer == "false":
        return False
    return None


def _text(value: object) -> str:
    """NVML answers in bytes on some versions and str on others."""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return str(value).strip()
