"""Running one Windows command and reading what it said back.

Everything the environment diagnosis learns about this machine comes through
here: PowerShell for the firmware, nvidia-smi for the driver, wsl.exe for WSL.
Each run is bounded by a timeout and an output limit, because a probe that hangs
is a Worker that never starts and a probe that returns a gigabyte is a Worker
that dies reading it.
"""

from __future__ import annotations

import asyncio
import codecs
import os
import subprocess
from dataclasses import dataclass
from typing import Protocol

from .errors import CommandError

#: The character UTF-16 leaves between every ASCII letter.
NUL = chr(0)


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    async def run(
        self, args: list[str], *, timeout: float, max_output_bytes: int
    ) -> CommandResult: ...


class AsyncSubprocessRunner:
    """The real thing. Raises :class:`CommandError` rather than hanging."""

    async def run(
        self, args: list[str], *, timeout: float, max_output_bytes: int
    ) -> CommandResult:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creationflags,
            )
        except FileNotFoundError as error:
            raise CommandError(
                "COMMAND_NOT_FOUND", f"{args[0]} was not found on this machine"
            ) from error
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        # asyncio.TimeoutError, not the builtin: they are the same class from
        # Python 3.11 but not on 3.10, where catching the builtin catches
        # nothing and the probe escapes as an unexpected fault.
        except asyncio.TimeoutError as error:
            process.kill()
            await process.wait()
            raise CommandError(
                "COMMAND_TIMEOUT", f"{args[0]} did not answer within {timeout}s"
            ) from error
        if len(stdout_bytes) + len(stderr_bytes) > max_output_bytes:
            raise CommandError(
                "OUTPUT_LIMIT_EXCEEDED", f"{args[0]} produced more output than expected"
            )
        return CommandResult(
            process.returncode,
            decode_output(stdout_bytes),
            decode_output(stderr_bytes),
        )


def decode_output(raw: bytes) -> str:
    """Text from a tool that may answer in UTF-8 or in UTF-16.

    nvidia-smi and PowerShell answer in UTF-8; wsl.exe on Windows writes UTF-16.
    Read as UTF-8 its ASCII survives by accident -- every second byte is a NUL
    and stripping those puts it back -- while everything else is destroyed,
    which is how a Korean Windows reported a failure whose reason could not be
    read.

    Decided by looking at the bytes rather than at which tool was run, so a
    command that goes through the same runner either way needs no special case.
    """
    if not raw:
        return ""
    if raw[:2] in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE):
        return raw.decode("utf-16", errors="replace")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        # Not UTF-8 at all. UTF-16 is the only other thing these tools produce.
        return raw.decode("utf-16le", errors="replace")
    # UTF-16 holding plain ASCII decodes as UTF-8 without complaint, because a
    # NUL is a valid character; every second byte being one gives it away.
    if NUL in text:
        return raw.decode("utf-16le", errors="replace")
    return text
