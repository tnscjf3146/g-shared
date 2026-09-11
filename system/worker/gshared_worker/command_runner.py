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
import logging
import os
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .errors import CommandError
from .logging_setup import LOGGER_NAME

_debug = logging.getLogger(LOGGER_NAME)

#: The character UTF-16 leaves between every ASCII letter.
NUL = chr(0)

#: How much of one line is read before it is handed over unterminated.
#:
#: A training container's stdout is shared with progress bars that never write
#: a newline at all. Without a cap, one of those is a single line that grows
#: for the length of the run.
LINE_LIMIT_BYTES = 64 * 1024

#: How much is read from a pipe at a time. Small enough that a line arrives
#: promptly, large enough not to syscall per character.
READ_CHUNK_BYTES = 4096


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    async def run(
        self, args: list[str], *, timeout: float, max_output_bytes: int
    ) -> CommandResult: ...


#: Called with one decoded line, while the process is still running.
OnLine = Callable[[str], None]


class StreamingCommandRunner(CommandRunner, Protocol):
    """A runner that can also report lines as they are written.

    Separate from :class:`CommandRunner` so that everything which only needs
    the finished output keeps the smaller contract. A caller that wants lines
    asks for them; a caller that does not is unaffected, and the fakes in the
    existing tests stay valid runners.
    """

    async def run_streaming(
        self,
        args: list[str],
        *,
        timeout: float,
        max_output_bytes: int,
        on_stdout_line: OnLine | None = None,
        on_stderr_line: OnLine | None = None,
    ) -> CommandResult: ...


class LineSplitter:
    """Bytes from a pipe in, whole lines out.

    Splits on a newline **and** on a carriage return. The carriage return
    is not decoration here: ``transformers`` and ``tqdm`` redraw a progress
    bar by returning to the start of the line, so a splitter that only knew
    about newlines would treat an entire run's worth of redraws as one
    unfinished line and hold it until the container exited.

    A line that reaches :data:`LINE_LIMIT_BYTES` without a terminator is
    handed over as it stands. That loses the boundary of something already
    pathological, and keeps this object's memory bounded either way.
    """

    def __init__(self, limit: int = LINE_LIMIT_BYTES) -> None:
        self._buffer = bytearray()
        self._limit = max(1, limit)

    def feed(self, chunk: bytes) -> list[bytes]:
        """Whatever complete lines this chunk finished."""
        lines: list[bytes] = []
        # Appended whole, so a terminator split across two chunks still works.
        self._buffer += chunk
        while True:
            cut = _first_terminator(self._buffer)
            if cut < 0:
                break
            width = 1
            if self._buffer[cut:cut + 1] == b"\r":
                if cut + 1 >= len(self._buffer):
                    # Might be the first half of a CRLF whose second half is
                    # in the next chunk. Splitting now would publish the line
                    # and then an empty one, which is how the first draft of
                    # this reported a blank event after every real line.
                    break
                if self._buffer[cut + 1:cut + 2] == b"\n":
                    width = 2
            lines.append(bytes(self._buffer[:cut]))
            del self._buffer[:cut + width]
        while len(self._buffer) >= self._limit:
            lines.append(bytes(self._buffer[:self._limit]))
            del self._buffer[:self._limit]
        return lines

    def flush(self) -> list[bytes]:
        """Whatever is left when the pipe closes, if anything."""
        if not self._buffer:
            return []
        remaining = bytes(self._buffer)
        self._buffer.clear()
        # A pipe that closed on a bare carriage return leaves one behind.
        if remaining.endswith(b"\r"):
            remaining = remaining[:-1]
        return [remaining] if remaining else []


def _first_terminator(buffer: bytearray) -> int:
    newline = buffer.find(b"\n")
    carriage = buffer.find(b"\r")
    if newline < 0:
        return carriage
    if carriage < 0:
        return newline
    return min(newline, carriage)


class TailBuffer:
    """The last N bytes of a stream, and no more.

    The non-streaming :meth:`AsyncSubprocessRunner.run` refuses output past
    its limit, which is right for a probe: a diagnosis that produced a
    gigabyte has gone wrong and its answer is worthless. It is the wrong
    answer for a training run. Hours of GPU time would be thrown away because
    a CUDA warning repeated too often, and the events that mattered were read
    live long before the limit was reached.

    So the streaming path keeps the same ceiling on memory and a different
    answer to reaching it: the oldest bytes go, the process runs on.
    """

    def __init__(self, max_bytes: int) -> None:
        self._max = max(0, max_bytes)
        self._buffer = bytearray()
        self.dropped = False

    def add(self, chunk: bytes) -> None:
        if not self._max:
            self.dropped = self.dropped or bool(chunk)
            return
        self._buffer += chunk
        if len(self._buffer) > self._max:
            del self._buffer[:len(self._buffer) - self._max]
            self.dropped = True

    def text(self) -> str:
        return decode_output(bytes(self._buffer))


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

    async def run_streaming(
        self,
        args: list[str],
        *,
        timeout: float,
        max_output_bytes: int,
        on_stdout_line: OnLine | None = None,
        on_stderr_line: OnLine | None = None,
    ) -> CommandResult:
        """The same command, with its lines handed over as they are written.

        **Both pipes are drained at once.** A reader that took stdout to the
        end and only then turned to stderr would hang the moment the child
        filled the stderr pipe's buffer -- the child blocks writing, the
        reader blocks reading a stdout that will never advance, and neither
        ever moves. A training container writes to both, so this is not a
        theoretical shape.

        Timeout, kill and exit code behave as they do in :meth:`run`. What
        differs is the output limit: see :class:`TailBuffer`.
        """
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

        # Half the ceiling each, so one noisy stream cannot crowd the other
        # out of the record and the total stays what the caller asked for.
        each = max(0, max_output_bytes) // 2
        out_tail, err_tail = TailBuffer(each), TailBuffer(each)

        drain = asyncio.gather(
            _drain(process.stdout, out_tail, on_stdout_line),
            _drain(process.stderr, err_tail, on_stderr_line),
        )
        try:
            await asyncio.wait_for(
                asyncio.gather(drain, process.wait()), timeout=timeout)
        except asyncio.TimeoutError as error:
            drain.cancel()
            process.kill()
            await process.wait()
            raise CommandError(
                "COMMAND_TIMEOUT", f"{args[0]} did not answer within {timeout}s"
            ) from error

        return CommandResult(
            process.returncode, out_tail.text(), err_tail.text())


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


async def _drain(
    stream: Any,
    tail: TailBuffer,
    on_line: OnLine | None,
) -> None:
    """Read one pipe to the end, reporting whole lines on the way.

    A callback that raises is logged and the read goes on. The alternative is
    that a bug in whoever is counting progress stops the pipe being read,
    which stops the container -- a training run lost to a progress bar.
    """
    if stream is None:
        return
    splitter = LineSplitter()
    while True:
        chunk = await stream.read(READ_CHUNK_BYTES)
        if not chunk:
            break
        tail.add(chunk)
        if on_line is None:
            continue
        for line in splitter.feed(chunk):
            _hand_over(on_line, line)
    if on_line is not None:
        for line in splitter.flush():
            _hand_over(on_line, line)


def _hand_over(on_line: OnLine, line: bytes) -> None:
    try:
        on_line(decode_output(line))
    except Exception:
        _debug.exception("command: a line reader raised; the read continues")
