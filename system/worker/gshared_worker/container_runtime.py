"""Whether a container in our distribution can actually reach the GPU.

This is the last question in the runtime chain and the only one that cannot be
answered by looking: a Docker Engine that answers, a toolkit that is configured
and a driver that is installed can all be true while ``--gpus all`` still fails.
So a container is really started, and it is asked what it can see.

An exit code of 0 is not the answer. A container that started and printed
nothing has proved nothing, so the report is only accepted when the GPU names
itself.

The Docker commands are flat ``["docker", ...]`` lists with no host paths and no
bind mounts, which is what lets the same list run inside the distribution
through :class:`~gshared_worker.wsl_runtime.WslCommandRunner`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .command_runner import CommandRunner
from .errors import WorkerError
from .wsl_runtime import OUTPUT_LIMIT, WslCommandRunner

#: The image the GPU probe runs in, pinned by digest so the check cannot drift
#: with a moving tag. It is NVIDIA's own CUDA base image, which carries
#: nvidia-smi and nothing else this probe needs.
GPU_PROBE_IMAGE = (
    "nvidia/cuda@sha256:"
    "6e6bcc811b807465076444f7018330976d56d7a6fc41574b4bc3babf411a7a98"
)

_ENGINE_TIMEOUT_SECONDS = 15
_PULL_TIMEOUT_SECONDS = 1800
_PROBE_TIMEOUT_SECONDS = 120
_PROBE_OUTPUT_LIMIT = 64 * 1024


class GpuOutcome(Enum):
    """What running a container told us about the GPU."""

    #: A container started and the GPU named itself.
    VISIBLE = "VISIBLE"
    #: The engine did not answer, so nothing could be run at all.
    ENGINE_UNAVAILABLE = "ENGINE_UNAVAILABLE"
    #: The engine answered but is not running Linux containers.
    LINUX_REQUIRED = "LINUX_REQUIRED"
    #: The image could not be fetched. Says nothing about the GPU.
    IMAGE_UNAVAILABLE = "IMAGE_UNAVAILABLE"
    #: A container started and could not see the GPU.
    GPU_UNAVAILABLE = "GPU_UNAVAILABLE"


@dataclass(frozen=True)
class GpuReport:
    """What the GPU said about itself, from inside a container."""

    model: str
    driver_version: str
    vram_mib: int | None = None

    def summary(self) -> str:
        vram = f", {self.vram_mib} MiB" if self.vram_mib is not None else ""
        return f"{self.model} (driver {self.driver_version}{vram})"


@dataclass(frozen=True)
class GpuVerification:
    outcome: GpuOutcome
    detail: str
    #: Present only when the outcome is VISIBLE.
    report: GpuReport | None = None

    @property
    def succeeded(self) -> bool:
        return self.outcome is GpuOutcome.VISIBLE


class ContainerGpuProbe:
    """Runs one throwaway container and reads what the GPU says.

    Nothing here raises for an expected outcome. Every way this can fail is one
    of :class:`GpuOutcome`, because the caller has to tell "the engine is down"
    from "the GPU is not reachable" -- those point at different fixes.
    """

    def __init__(
        self,
        inside: CommandRunner | None = None,
        *,
        image: str = GPU_PROBE_IMAGE,
    ) -> None:
        self._inside = inside or WslCommandRunner()
        self._image = image

    async def verify(self) -> GpuVerification:
        version = await self._docker(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            _ENGINE_TIMEOUT_SECONDS,
        )
        if version is None or version.exit_code != 0:
            return GpuVerification(
                GpuOutcome.ENGINE_UNAVAILABLE,
                "the Docker Engine in the distribution did not answer",
            )

        info = await self._docker(
            ["docker", "info", "--format", "{{.OSType}}"], _ENGINE_TIMEOUT_SECONDS
        )
        if info is None or info.exit_code != 0 or info.stdout.strip() != "linux":
            return GpuVerification(
                GpuOutcome.LINUX_REQUIRED,
                "the engine is not running Linux containers",
            )

        if not await self._image_present():
            pulled = await self._docker(
                ["docker", "pull", self._image], _PULL_TIMEOUT_SECONDS
            )
            if pulled is None or pulled.exit_code != 0 or not await self._image_present():
                return GpuVerification(
                    GpuOutcome.IMAGE_UNAVAILABLE,
                    "the GPU probe image could not be fetched, so the GPU was "
                    "never tested",
                )

        probe = await self._docker([
            "docker", "run", "--rm", "--gpus", "all", "--network", "none",
            "--read-only", "--cap-drop", "ALL", "--security-opt",
            "no-new-privileges", self._image, "nvidia-smi",
            "--query-gpu=name,driver_version,memory.total",
            "--format=csv,noheader,nounits",
        ], _PROBE_TIMEOUT_SECONDS)
        if probe is None or probe.exit_code != 0:
            return GpuVerification(
                GpuOutcome.GPU_UNAVAILABLE,
                "a container started but could not see the GPU",
            )

        report = parse_gpu_report(probe.stdout)
        if report is None:
            # Exit code 0 is not the answer. A container that printed nothing
            # usable has proved nothing about the GPU.
            return GpuVerification(
                GpuOutcome.GPU_UNAVAILABLE,
                "the container ran but named no GPU",
            )
        return GpuVerification(GpuOutcome.VISIBLE, report.summary(), report)

    async def _image_present(self) -> bool:
        result = await self._docker(
            ["docker", "image", "inspect", self._image, "--format", "{{json .Id}}"],
            _ENGINE_TIMEOUT_SECONDS,
        )
        return result is not None and result.exit_code == 0

    async def _docker(self, args: list[str], timeout: float):
        """Run one Docker command inside the distribution.

        Returns None for a command that could not be run at all, which the
        caller reads the same way as a non-zero exit: this did not answer.
        """
        limit = OUTPUT_LIMIT if args[1:2] == ["pull"] else _PROBE_OUTPUT_LIMIT
        try:
            return await self._inside.run(
                args, timeout=timeout, max_output_bytes=limit
            )
        except (FileNotFoundError, WorkerError):
            return None


def parse_gpu_report(stdout: str) -> GpuReport | None:
    """The first GPU nvidia-smi listed, or None when it named none.

    ``--format=csv,noheader,nounits`` gives one line per GPU, so the first line
    is enough: this asks whether *a* GPU is reachable, not how many there are.
    """
    for line in stdout.replace("\x00", "").splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        vram: int | None = None
        if len(parts) >= 3 and parts[2].isdigit():
            vram = int(parts[2])
        return GpuReport(parts[0], parts[1], vram)
    return None
