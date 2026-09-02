"""Build STEP 2 wire payloads from current GPU state and Worker metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from .errors import WorkerConfigurationError
from .benchmark_models import BenchmarkResult
from .runtime import WorkerGpuState


class GpuStateProvider(Protocol):
    def snapshot(self) -> WorkerGpuState: ...


@dataclass(frozen=True)
class WorkerMetadata:
    worker_id: str
    hourly_price: int
    supported_workloads: tuple[str, ...]
    worker_status: str = "IDLE"

    def __post_init__(self) -> None:
        if not self.worker_id or self.worker_id.isspace():
            raise WorkerConfigurationError("worker_id must not be blank")
        if self.hourly_price < 0:
            raise WorkerConfigurationError("hourly_price must be non-negative")
        if not self.supported_workloads or any(
            not workload or workload.isspace()
            for workload in self.supported_workloads
        ):
            raise WorkerConfigurationError(
                "supported_workloads must contain only non-blank values"
            )
        if self.worker_status not in {"IDLE", "BUSY"}:
            raise WorkerConfigurationError("worker_status must be IDLE or BUSY")


class NvmlWorkerMessageSource:
    def __init__(
        self,
        provider: GpuStateProvider,
        metadata: WorkerMetadata,
        status_provider: Callable[[], str] | None = None,
        benchmark: BenchmarkResult | None = None,
    ) -> None:
        self._provider = provider
        self._metadata = metadata
        self._status_provider = status_provider or (lambda: metadata.worker_status)
        self._benchmark = benchmark

    def register_payload(self) -> dict[str, object]:
        state = self._provider.snapshot()
        payload: dict[str, object] = {
            "type": "REGISTER",
            "workerId": self._metadata.worker_id,
            "gpuModel": state.gpu_model,
            "totalVramMb": state.total_vram_mb,
            "freeVramMb": state.free_vram_mb,
            "gpuUtilization": state.gpu_utilization,
            "cudaAvailable": state.cuda_available,
            "supportedWorkloads": list(self._metadata.supported_workloads),
            "hourlyPrice": self._metadata.hourly_price,
            "workerStatus": self._status_provider(),
        }
        if self._benchmark is not None:
            payload["benchmark"] = self._benchmark.to_register_payload()
        return payload

    def heartbeat_payload(self) -> dict[str, object]:
        state = self._provider.snapshot()
        return {
            "type": "HEARTBEAT",
            "workerId": self._metadata.worker_id,
            "freeVramMb": state.free_vram_mb,
            "gpuUtilization": state.gpu_utilization,
            "workerStatus": self._status_provider(),
        }
