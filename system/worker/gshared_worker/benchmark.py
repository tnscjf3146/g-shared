"""Measuring what this machine can actually do, in tokens per second.

The number this produces is what the scheduler prices and plans with, so the
one rule that matters is: **it is measured or it does not exist**. There is no
fallback average, no table of GPU models, no zero. Every way the measurement
can fail is a failure, and a Worker that fails it does not get registered.

The work happens where every other container in this program happens -- inside
the ``gshared-worker`` distribution, through the same
:class:`~gshared_worker.wsl_runtime.WslCommandRunner` and the same hardened
``docker run``. Nothing here talks to CUDA on Windows, and nothing here is a
second Docker abstraction.

What is timed is described in :mod:`gshared_worker.benchmark_program`. This
module's job is to start that program, refuse anything it cannot trust, and
hand back one number.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from enum import Enum

from .benchmark_program import (
    BENCHMARK_PROGRAM,
    BENCHMARK_VERSION,
    MEASURED_STEPS,
    PROCESSED_TOKENS_PER_RUN,
    RESULT_MARKER,
    RUNS,
)
from .command_runner import CommandRunner
from .errors import WorkerError
from .logging_setup import LOGGER_NAME
from .wsl_runtime import WslCommandRunner

_debug = logging.getLogger(LOGGER_NAME)

#: The runtime the workload runs in, pinned by digest. A moving tag would mean
#: two Workers measured by different PyTorch versions, whose numbers could not
#: be compared. This is the same image the PoC published, which already carries
#: PyTorch with CUDA; the program inside it is ours.
BENCHMARK_IMAGE = (
    "le3560/gpu-pytorch-poc-30s@sha256:"
    "d7fe2de03ff8532900c600d9620cd7d57fcedbeb6531b3acc2b53a9f09a71a49"
)

_ENGINE_TIMEOUT_SECONDS = 15
_PULL_TIMEOUT_SECONDS = 3600
#: Generous: three runs take about ten seconds of GPU time on an RTX 4070, but
#: a slower card doing the same fixed work takes proportionally longer.
_BENCHMARK_TIMEOUT_SECONDS = 900
_OUTPUT_LIMIT = 256 * 1024


class BenchmarkOutcome(Enum):
    """How the measurement went. Only one of these is a number."""

    MEASURED = "MEASURED"
    #: The engine did not answer, so nothing could be run.
    ENGINE_UNAVAILABLE = "ENGINE_UNAVAILABLE"
    #: The runtime image could not be fetched. Says nothing about the GPU.
    IMAGE_UNAVAILABLE = "IMAGE_UNAVAILABLE"
    #: A container started and could not use the GPU for this workload.
    GPU_UNAVAILABLE = "GPU_UNAVAILABLE"
    #: It ran too long and was given up on.
    TIMED_OUT = "TIMED_OUT"
    #: It answered, and the answer could not be trusted.
    MALFORMED = "MALFORMED"
    #: It ran and failed inside.
    FAILED = "FAILED"


@dataclass(frozen=True)
class BenchmarkResult:
    """One measurement. ``tokens_per_sec`` is the whole point of it."""

    tokens_per_sec: float
    processed_tokens: int
    elapsed_seconds: float
    measured_steps: int
    batch_size: int
    sequence_length: int
    #: Every run's rate, so a wide spread can be seen rather than hidden by the
    #: median.
    run_rates: tuple[float, ...] = ()
    peak_vram_mb: int | None = None

    def summary(self) -> str:
        return (f"{self.tokens_per_sec:,.0f} tokens/sec "
                f"({self.processed_tokens:,} tokens in "
                f"{self.elapsed_seconds:.2f}s of GPU time)")


@dataclass(frozen=True)
class BenchmarkReport:
    outcome: BenchmarkOutcome
    detail: str
    #: Present only when the outcome is MEASURED.
    result: BenchmarkResult | None = None

    @property
    def succeeded(self) -> bool:
        return self.outcome is BenchmarkOutcome.MEASURED


class GpuBenchmark:
    """Runs the fixed workload in one throwaway container and reads the number.

    Like :class:`~gshared_worker.container_runtime.ContainerGpuProbe`, nothing
    here raises for an expected outcome: the caller has to tell "the engine is
    down" from "this GPU cannot do the work", because those point at different
    fixes and only one of them means the machine is unsuitable.
    """

    def __init__(
        self,
        inside: CommandRunner | None = None,
        *,
        image: str = BENCHMARK_IMAGE,
    ) -> None:
        self._inside = inside or WslCommandRunner()
        self._image = image

    async def measure(self) -> BenchmarkReport:
        engine = await self._docker(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            _ENGINE_TIMEOUT_SECONDS,
        )
        if engine is None or engine.exit_code != 0:
            return BenchmarkReport(
                BenchmarkOutcome.ENGINE_UNAVAILABLE,
                "the Docker Engine in the distribution did not answer")

        if not await self._image_present():
            # Fetching is not part of the measurement: the clock inside the
            # container starts long after this.
            _debug.info("benchmark: fetching the runtime image")
            pulled = await self._docker(
                ["docker", "pull", self._image], _PULL_TIMEOUT_SECONDS)
            if pulled is None or pulled.exit_code != 0 or not await self._image_present():
                return BenchmarkReport(
                    BenchmarkOutcome.IMAGE_UNAVAILABLE,
                    "the benchmark runtime image could not be fetched, so the "
                    "GPU was never measured")

        _debug.info("benchmark: running %s (%s runs of %s steps)",
                    BENCHMARK_VERSION, RUNS, MEASURED_STEPS)
        finished = await self._docker(_command(self._image), _BENCHMARK_TIMEOUT_SECONDS)
        if finished is None:
            return BenchmarkReport(
                BenchmarkOutcome.TIMED_OUT,
                "the benchmark did not finish in time and was given up on")

        report = read_result(finished.stdout)
        if report.outcome is BenchmarkOutcome.FAILED and finished.exit_code == 0:
            # It said it failed but the container succeeded, or vice versa.
            # Either way the answer is not a measurement.
            _debug.info("benchmark: container exit 0 with a failed result")
        _debug.info("benchmark: %s -- %s", report.outcome.value, report.detail)
        return report

    async def _image_present(self) -> bool:
        found = await self._docker(
            ["docker", "image", "inspect", self._image, "--format", "{{json .Id}}"],
            _ENGINE_TIMEOUT_SECONDS)
        return found is not None and found.exit_code == 0

    async def _docker(self, args: list[str], timeout: float):
        """Run one Docker command inside the distribution.

        None means it could not be run at all, which the caller reads the same
        way as a non-zero exit: this did not answer.
        """
        try:
            return await self._inside.run(
                args, timeout=timeout, max_output_bytes=_OUTPUT_LIMIT)
        except (FileNotFoundError, WorkerError):
            return None


def _command(image: str) -> list[str]:
    """The one container this module ever starts.

    Hardened the same way as the GPU probe, plus what this workload needs and
    nothing more: a small writable ``/tmp`` because PyTorch insists on a cache
    directory, and a ``USER`` because its cache-naming calls ``getpass.getuser``
    and the image's own uid is not in ``/etc/passwd``. No host path is mounted
    and there is no network.
    """
    return [
        "docker", "run", "--rm",
        "--gpus", "all",
        "--network", "none",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", "256",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=256m",
        "-e", "HOME=/tmp",
        "-e", "USER=gshared",
        "-e", "TORCHINDUCTOR_CACHE_DIR=/tmp/inductor",
        "-e", "TRITON_CACHE_DIR=/tmp/triton",
        "--entrypoint", "python",
        image, "-c", BENCHMARK_PROGRAM,
    ]


def read_result(stdout: str) -> BenchmarkReport:
    """The measurement the container printed, or why it cannot be believed.

    Every rejection here is deliberate. A benchmark that returns a number
    nobody measured is worse than one that returns nothing, because the
    scheduler cannot tell the difference.
    """
    line = _marked_line(stdout)
    if line is None:
        return BenchmarkReport(
            BenchmarkOutcome.MALFORMED,
            "the benchmark printed no result")
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return BenchmarkReport(
            BenchmarkOutcome.MALFORMED, "the benchmark result was not JSON")
    if not isinstance(payload, dict):
        return BenchmarkReport(
            BenchmarkOutcome.MALFORMED, "the benchmark result was not an object")

    if payload.get("benchmarkVersion") != BENCHMARK_VERSION:
        # A number from a different workload is not comparable with the others.
        return BenchmarkReport(
            BenchmarkOutcome.MALFORMED,
            f"the benchmark result is not {BENCHMARK_VERSION}")

    if payload.get("status") != "OK":
        code = payload.get("errorCode") or "BENCHMARK_FAILED"
        message = payload.get("errorMessage") or "the benchmark failed"
        outcome = (BenchmarkOutcome.GPU_UNAVAILABLE
                   if code in ("BENCHMARK_CUDA_UNAVAILABLE", "BENCHMARK_GPU_UNSUPPORTED")
                   else BenchmarkOutcome.FAILED)
        return BenchmarkReport(outcome, f"{code}: {message}")

    rate = _positive_number(payload.get("tokensPerSec"))
    if rate is None:
        return BenchmarkReport(
            BenchmarkOutcome.MALFORMED,
            "the benchmark did not report a usable tokens/sec")

    processed = _positive_integer(payload.get("processedTokens"))
    elapsed = _positive_number(payload.get("elapsedSeconds"))
    steps = _positive_integer(payload.get("measuredSteps"))
    batch = _positive_integer(payload.get("batchSize"))
    length = _positive_integer(payload.get("sequenceLength"))
    if None in (processed, elapsed, steps, batch, length):
        return BenchmarkReport(
            BenchmarkOutcome.MALFORMED,
            "the benchmark result is missing what the number was measured from")

    if processed != PROCESSED_TOKENS_PER_RUN or steps != MEASURED_STEPS:
        # The workload inside the container is not the one this Worker thinks
        # it ran, so the number cannot be compared with anybody else's.
        return BenchmarkReport(
            BenchmarkOutcome.MALFORMED,
            f"the benchmark measured {processed} tokens over {steps} steps, "
            f"not the fixed {PROCESSED_TOKENS_PER_RUN} over {MEASURED_STEPS}")

    rates = _run_rates(payload.get("runs"))
    if len(rates) != RUNS:
        return BenchmarkReport(
            BenchmarkOutcome.MALFORMED,
            f"the benchmark reported {len(rates)} usable runs, not {RUNS}")

    return BenchmarkReport(
        BenchmarkOutcome.MEASURED,
        f"{rate:,.0f} tokens/sec over {RUNS} runs",
        BenchmarkResult(
            tokens_per_sec=rate,
            processed_tokens=processed,
            elapsed_seconds=elapsed,
            measured_steps=steps,
            batch_size=batch,
            sequence_length=length,
            run_rates=rates,
            peak_vram_mb=_positive_integer(payload.get("peakVramMb")),
        ),
    )


def _marked_line(stdout: str) -> str | None:
    """The last marked line. The container may print other things first."""
    found = None
    for line in stdout.replace("\x00", "").splitlines():
        stripped = line.strip()
        if stripped.startswith(RESULT_MARKER):
            found = stripped[len(RESULT_MARKER):]
    return found


def _run_rates(runs: object) -> tuple[float, ...]:
    if not isinstance(runs, list):
        return ()
    rates = []
    for entry in runs:
        if not isinstance(entry, dict):
            return ()
        rate = _positive_number(entry.get("tokensPerSec"))
        if rate is None:
            return ()
        rates.append(rate)
    return tuple(rates)


def _positive_number(value: object) -> float | None:
    """A real, finite, positive number. Zero, NaN and infinity are not."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _positive_integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value
