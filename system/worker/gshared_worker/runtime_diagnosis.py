"""What is missing inside our own distribution before it can run GPU work.

The same shape as :mod:`gshared_worker.diagnosis` -- one answer per
prerequisite, dependencies known, nothing reported as failing that was never
really tested -- but about the runtime we build rather than about the machine
we found. :class:`~gshared_worker.diagnosis.Finding` is reused unchanged: what
"MISSING" and "BLOCKED" mean does not change because the subject did.

Why a separate enum and a separate diagnosis: the host checks are read-only and
about a machine nobody may alter, while these are about a distribution this
program owns and may install into. Judging both in one pass would put the
supplier's firmware and our apt packages behind the same verdict.

**CONTAINER_GPU is the one check here that is not read-only** -- it pulls an
image the first time and runs a container. That is deliberate: nothing short of
running one proves a container can reach the GPU.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .config import WSL_DISTRO_NAME
from .diagnosis import Finding


class RuntimePrerequisite(Enum):
    """Something our distribution needs before it can take GPU work."""

    GSHARED_DISTRO = "GSHARED_DISTRO"
    DOCKER_ENGINE = "DOCKER_ENGINE"
    CONTAINER_TOOLKIT = "CONTAINER_TOOLKIT"
    CONTAINER_GPU = "CONTAINER_GPU"


@dataclass(frozen=True)
class RuntimeCheck:
    prerequisite: RuntimePrerequisite
    finding: Finding
    detail: str
    remedy: str | None = None

    @property
    def is_resolved(self) -> bool:
        return self.finding is Finding.SATISFIED


@dataclass(frozen=True)
class RuntimeDiagnosis:
    checks: tuple[RuntimeCheck, ...]

    @property
    def is_ready(self) -> bool:
        return all(check.is_resolved for check in self.checks)

    @property
    def blocked(self) -> tuple[RuntimeCheck, ...]:
        """What the person has to deal with before anything else can proceed."""
        return tuple(c for c in self.checks if c.finding is Finding.BLOCKED)

    @property
    def resolvable(self) -> tuple[RuntimeCheck, ...]:
        """What this program may put right, in the order it must be done."""
        return tuple(
            c for c in self.checks
            if c.finding in (Finding.MISSING, Finding.INACTIVE)
        )

    @property
    def undetermined(self) -> tuple[RuntimeCheck, ...]:
        return tuple(c for c in self.checks if c.finding is Finding.UNDETERMINED)

    def find(self, prerequisite: RuntimePrerequisite) -> RuntimeCheck:
        for check in self.checks:
            if check.prerequisite is prerequisite:
                return check
        raise KeyError(prerequisite)


class DistroProbe(Protocol):
    async def state(self) -> object: ...


class RuntimeProbe(Protocol):
    async def engine_state(self) -> str: ...
    async def toolkit_configured(self) -> bool: ...


class GpuProbe(Protocol):
    async def verify(self) -> object: ...


class RuntimeDiagnosisRunner:
    """Judges every runtime prerequisite, and knows which rest on which."""

    def __init__(
        self, distro: DistroProbe, runtime: RuntimeProbe, gpu: GpuProbe
    ) -> None:
        self._distro = distro
        self._runtime = runtime
        self._gpu = gpu

    async def run(self) -> RuntimeDiagnosis:
        found: dict[RuntimePrerequisite, RuntimeCheck] = {}
        #: Prerequisites never tested because something they rest on was
        #: unresolved. Kept apart from the ones that were tested and simply
        #: could not be read: those two share a finding but not a meaning.
        skipped: set[RuntimePrerequisite] = set()

        def skip(
            prerequisite: RuntimePrerequisite, blocker: RuntimePrerequisite
        ) -> RuntimeCheck:
            skipped.add(prerequisite)
            return RuntimeCheck(
                prerequisite, Finding.UNDETERMINED,
                f"Not tested: {blocker.value} is unresolved.",
            )

        def unresolved(
            *needed: RuntimePrerequisite,
        ) -> RuntimePrerequisite | None:
            for prerequisite in needed:
                if prerequisite in skipped:
                    return prerequisite
                check = found.get(prerequisite)
                if check is not None and check.finding in (
                    Finding.MISSING, Finding.INACTIVE, Finding.BLOCKED
                ):
                    return prerequisite
            return None

        def record(check: RuntimeCheck) -> None:
            found[check.prerequisite] = check

        record(await self._distribution())
        record(await self._engine(
            skip, unresolved(RuntimePrerequisite.GSHARED_DISTRO)))
        record(await self._toolkit(
            skip, unresolved(RuntimePrerequisite.DOCKER_ENGINE)))
        record(await self._container_gpu(
            skip, unresolved(RuntimePrerequisite.CONTAINER_TOOLKIT)))

        return RuntimeDiagnosis(tuple(found[p] for p in RuntimePrerequisite))

    async def _distribution(self) -> RuntimeCheck:
        state = await self._distro.state()
        if not state.exists:
            return RuntimeCheck(
                RuntimePrerequisite.GSHARED_DISTRO, Finding.MISSING,
                f"{WSL_DISTRO_NAME} does not exist yet.",
                f"Create the {WSL_DISTRO_NAME} distribution. Nothing the "
                "supplier installed for themselves is touched.",
            )
        if not state.is_usable:
            return RuntimeCheck(
                RuntimePrerequisite.GSHARED_DISTRO, Finding.INACTIVE,
                f"{WSL_DISTRO_NAME} is registered as WSL version {state.version}.",
                "Convert it to WSL2. Version 1 cannot reach the GPU.",
            )
        return RuntimeCheck(
            RuntimePrerequisite.GSHARED_DISTRO, Finding.SATISFIED,
            f"{WSL_DISTRO_NAME} is registered on WSL2.",
        )

    async def _engine(self, skip, blocker) -> RuntimeCheck:
        if blocker is not None:
            return skip(RuntimePrerequisite.DOCKER_ENGINE, blocker)
        state = await self._runtime.engine_state()
        if state == "running":
            return RuntimeCheck(
                RuntimePrerequisite.DOCKER_ENGINE, Finding.SATISFIED,
                f"The Docker Engine inside {WSL_DISTRO_NAME} is answering.",
            )
        if state == "installed":
            # Present but the daemon is not up. Starting is ours to do, and it
            # is a different action from installing -- installing over a
            # working Docker is how a working Docker gets replaced.
            return RuntimeCheck(
                RuntimePrerequisite.DOCKER_ENGINE, Finding.INACTIVE,
                "Docker is installed in the distribution but not running.",
                "Start the Docker Engine inside the distribution.",
            )
        return RuntimeCheck(
            RuntimePrerequisite.DOCKER_ENGINE, Finding.MISSING,
            "The distribution has no Docker Engine.",
            "Install the Docker Engine inside the distribution.",
        )

    async def _toolkit(self, skip, blocker) -> RuntimeCheck:
        if blocker is not None:
            return skip(RuntimePrerequisite.CONTAINER_TOOLKIT, blocker)
        if await self._runtime.toolkit_configured():
            return RuntimeCheck(
                RuntimePrerequisite.CONTAINER_TOOLKIT, Finding.SATISFIED,
                "The NVIDIA container toolkit is installed and Docker uses it.",
            )
        return RuntimeCheck(
            RuntimePrerequisite.CONTAINER_TOOLKIT, Finding.MISSING,
            "Docker in the distribution cannot hand a GPU to a container.",
            "Install the NVIDIA container toolkit and configure the Docker runtime.",
        )

    async def _container_gpu(self, skip, blocker) -> RuntimeCheck:
        if blocker is not None:
            return skip(RuntimePrerequisite.CONTAINER_GPU, blocker)
        verified = await self._gpu.verify()
        if verified.succeeded:
            return RuntimeCheck(
                RuntimePrerequisite.CONTAINER_GPU, Finding.SATISFIED,
                f"A container can see the GPU: {verified.detail}",
            )
        if verified.outcome.value == "GPU_UNAVAILABLE":
            return RuntimeCheck(
                RuntimePrerequisite.CONTAINER_GPU, Finding.BLOCKED,
                verified.detail,
                "Check that the NVIDIA driver supports WSL and that the "
                "container toolkit is configured for the Docker runtime.",
            )
        # Anything else -- an engine that stopped answering between checks, a
        # failed image pull -- says nothing about the GPU. Calling it a GPU
        # fault would send somebody after their driver for a network problem.
        return RuntimeCheck(
            RuntimePrerequisite.CONTAINER_GPU, Finding.UNDETERMINED,
            f"The GPU could not be tested: {verified.detail}",
        )
