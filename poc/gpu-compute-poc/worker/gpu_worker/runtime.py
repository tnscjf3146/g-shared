"""Strict CUDA and NVML status provider for STEP 2 registration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .embedding import load_torch
from .errors import CudaUnavailableError, GpuInspectionError
from .gpu import NvmlGpuProbe


@dataclass(frozen=True)
class WorkerGpuState:
    gpu_uuid: str
    gpu_model: str
    total_vram_mb: int
    free_vram_mb: int
    gpu_utilization: int
    cuda_available: bool


class CudaGpuStatusProvider:
    """Own one NVML probe after first proving PyTorch CUDA availability."""

    def __init__(
        self,
        gpu_index: int = 0,
        *,
        torch_loader: Callable[[], Any] = load_torch,
        probe_factory: Callable[[int], Any] = NvmlGpuProbe,
    ) -> None:
        self._gpu_index = gpu_index
        self._torch_loader = torch_loader
        self._probe_factory = probe_factory
        self._probe_manager: Any | None = None
        self._probe: Any | None = None

    def __enter__(self) -> "CudaGpuStatusProvider":
        torch_module = self._torch_loader()
        if not bool(torch_module.cuda.is_available()):
            raise CudaUnavailableError(
                "CUDA unavailable: torch.cuda.is_available() is false"
            )

        self._open_probe()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._close_probe(exc_type, exc, traceback)

    def _open_probe(self) -> None:
        manager = self._probe_factory(self._gpu_index)
        probe = manager.__enter__()
        self._probe_manager = manager
        self._probe = probe

    def _close_probe(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        manager = self._probe_manager
        self._probe_manager = None
        self._probe = None
        if manager is not None:
            manager.__exit__(exc_type, exc, traceback)

    def snapshot(self) -> WorkerGpuState:
        if self._probe is None:
            raise GpuInspectionError(
                "CUDA GPU status provider must be opened before reading metrics"
            )
        try:
            snapshot = self._probe.snapshot()
        except GpuInspectionError as error:
            self._close_probe(type(error), error, error.__traceback__)
            self._open_probe()
            snapshot = self._probe.snapshot()
        return WorkerGpuState(
            gpu_uuid=snapshot.uuid,
            gpu_model=snapshot.name,
            total_vram_mb=snapshot.total_vram_mb,
            free_vram_mb=snapshot.free_vram_mb,
            gpu_utilization=snapshot.utilization_percent,
            cuda_available=True,
        )


class NvmlGpuStatusProvider(CudaGpuStatusProvider):
    """NVML-only provider for the Docker runtime agent.

    CUDA usability is proven by EnvironmentManager inside the pinned probe
    container, so the host does not need a PyTorch installation.
    """

    def __enter__(self) -> "NvmlGpuStatusProvider":
        self._open_probe()
        return self
