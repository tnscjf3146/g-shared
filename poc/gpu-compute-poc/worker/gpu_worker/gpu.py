"""NVIDIA GPU inspection and utilization sampling through NVML."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Thread
from typing import Any

from .errors import DependencyError, GpuInspectionError

BYTES_PER_MIB = 1024 * 1024


@dataclass(frozen=True)
class GpuSnapshot:
    index: int
    name: str
    total_vram_mb: int
    free_vram_mb: int
    utilization_percent: int


class NvmlGpuProbe:
    """Owns an NVML session and reads one physical NVIDIA GPU."""

    def __init__(self, index: int = 0, nvml_module: Any | None = None) -> None:
        self.index = index
        self._nvml = nvml_module
        self._handle: Any | None = None
        self._initialized = False

    def __enter__(self) -> "NvmlGpuProbe":
        if self._nvml is None:
            try:
                import pynvml
            except ImportError as exc:
                raise DependencyError(
                    "nvidia-ml-py is not installed; install worker requirements first"
                ) from exc
            self._nvml = pynvml

        try:
            self._nvml.nvmlInit()
            self._initialized = True
            count = self._nvml.nvmlDeviceGetCount()
            if count == 0:
                raise GpuInspectionError("NVML reports zero NVIDIA GPUs")
            if self.index < 0 or self.index >= count:
                raise GpuInspectionError(
                    f"GPU index {self.index} is invalid; NVML device count is {count}"
                )
            self._handle = self._nvml.nvmlDeviceGetHandleByIndex(self.index)
            return self
        except GpuInspectionError:
            self._shutdown()
            raise
        except Exception as exc:
            self._shutdown()
            raise GpuInspectionError(f"NVML initialization failed: {exc}") from exc

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._shutdown()

    def _shutdown(self) -> None:
        if self._initialized:
            try:
                self._nvml.nvmlShutdown()
            finally:
                self._initialized = False
                self._handle = None

    def snapshot(self) -> GpuSnapshot:
        if self._handle is None:
            raise GpuInspectionError("NVML probe must be opened before reading metrics")
        try:
            raw_name = self._nvml.nvmlDeviceGetName(self._handle)
            name = raw_name.decode("utf-8") if isinstance(raw_name, bytes) else str(raw_name)
            memory = self._nvml.nvmlDeviceGetMemoryInfo(self._handle)
            utilization = self._nvml.nvmlDeviceGetUtilizationRates(self._handle)
            return GpuSnapshot(
                index=self.index,
                name=name,
                total_vram_mb=round(memory.total / BYTES_PER_MIB),
                free_vram_mb=round(memory.free / BYTES_PER_MIB),
                utilization_percent=int(utilization.gpu),
            )
        except Exception as exc:
            raise GpuInspectionError(f"NVML metric read failed: {exc}") from exc


class GpuUtilizationSampler:
    """Samples GPU utilization during model load and embedding execution."""

    def __init__(self, probe: NvmlGpuProbe, interval_seconds: float = 0.05) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be greater than zero")
        self._probe = probe
        self._interval_seconds = interval_seconds
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._peak = 0
        self._error: Exception | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("GPU utilization sampler is already running")
        self._stop_event.clear()
        self._thread = Thread(target=self._sample_loop, name="gpu-utilization-sampler", daemon=True)
        self._thread.start()

    def _sample_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                self._peak = max(self._peak, self._probe.snapshot().utilization_percent)
                self._stop_event.wait(self._interval_seconds)
        except Exception as exc:  # propagated by stop()
            self._error = exc

    def stop(self) -> int:
        if self._thread is None:
            return self._peak
        self._stop_event.set()
        self._thread.join(timeout=max(1.0, self._interval_seconds * 4))
        self._thread = None
        if self._error is not None:
            error = self._error
            self._error = None
            raise GpuInspectionError(f"GPU utilization sampling failed: {error}") from error
        return self._peak

