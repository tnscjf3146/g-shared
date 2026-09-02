"""Mandatory startup benchmark orchestration, independent of WebSocket jobs."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from .benchmark_cache import BenchmarkCache
from .benchmark_models import BENCHMARK_VERSION, BenchmarkResult
from .docker_manager import DockerManager
from .errors import BenchmarkError, DockerManagerError
from .runtime import WorkerGpuState
from .workloads import GPU_STANDARD_BENCHMARK, get_system_workload_spec


class BenchmarkRunner:
    def __init__(
        self,
        docker: DockerManager,
        cache: BenchmarkCache,
        *,
        gpu_index: int,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        logger: Callable[[str], None] = print,
    ) -> None:
        self._docker = docker
        self._cache = cache
        self._gpu_index = gpu_index
        self._clock = clock
        self._logger = logger

    async def ensure(self, gpu: WorkerGpuState) -> BenchmarkResult:
        self._logger("[benchmark] checking cached benchmark")
        cached = self._cache.load(
            gpu_uuid=gpu.gpu_uuid,
            benchmark_version=BENCHMARK_VERSION,
        )
        if cached is not None:
            self._logger(f"[benchmark] cache hit score={cached.benchmark_score:.6f}")
            return cached

        self._logger("[benchmark] cache miss")
        self._logger(f"[benchmark] running {BENCHMARK_VERSION}")
        try:
            execution = await self._docker.execute_benchmark(
                get_system_workload_spec(GPU_STANDARD_BENCHMARK),
                gpu_index=self._gpu_index,
            )
        except DockerManagerError as error:
            code = error.code if error.code.startswith("BENCHMARK_") else "BENCHMARK_FAILED"
            raise BenchmarkError(code, f"{code}: {error}") from error

        measured_at = self._clock().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        payload = {
            **execution.payload,
            "gpuUuid": gpu.gpu_uuid,
            "gpuModel": gpu.gpu_model,
            "vramMb": gpu.total_vram_mb,
            "measuredAt": measured_at,
        }
        result = BenchmarkResult.from_mapping(payload)
        self._logger(f"[benchmark] completed score={result.benchmark_score:.6f}")
        self._cache.save(result)
        self._logger(f"[benchmark] saved {self._cache.path.name}")
        return result
