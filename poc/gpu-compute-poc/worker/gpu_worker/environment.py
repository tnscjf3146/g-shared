"""Startup readiness gate; it never installs or starts Docker."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class SetupStatus(Enum):
    READY = "READY"
    SETUP_REQUIRED = "SETUP_REQUIRED"
    FATAL = "FATAL"


@dataclass(frozen=True)
class EnvironmentReport:
    status: SetupStatus
    error_code: str | None
    message: str


class GpuProvider(Protocol):
    def snapshot(self) -> object: ...


class DockerEnvironmentProbe(Protocol):
    async def check_environment(self) -> None: ...


class EnvironmentManager:
    def __init__(self, gpu_provider: GpuProvider, docker: DockerEnvironmentProbe) -> None:
        self._gpu_provider = gpu_provider
        self._docker = docker

    async def inspect(self) -> EnvironmentReport:
        try:
            self._gpu_provider.snapshot()
        except Exception as error:
            return EnvironmentReport(
                SetupStatus.SETUP_REQUIRED,
                "NVIDIA_GPU_UNAVAILABLE",
                str(error),
            )
        try:
            await self._docker.check_environment()
        except Exception as error:
            code = getattr(error, "code", "DOCKER_ENVIRONMENT_UNAVAILABLE")
            return EnvironmentReport(SetupStatus.SETUP_REQUIRED, code, str(error))
        return EnvironmentReport(SetupStatus.READY, None, "Environment ready")
