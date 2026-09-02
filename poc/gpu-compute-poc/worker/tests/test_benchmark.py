from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

from gpu_worker.benchmark_cache import BenchmarkCache
from gpu_worker.benchmark_models import BenchmarkResult
from gpu_worker.benchmark_runner import BenchmarkRunner
from gpu_worker.docker_manager import DockerExecutionResult
from gpu_worker.errors import BenchmarkError, DockerManagerError
from gpu_worker.runtime import WorkerGpuState


def payload(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "benchmarkVersion": "gpu-benchmark-v1",
        "gpuUuid": "GPU-test-uuid",
        "gpuModel": "NVIDIA Test GPU",
        "vramMb": 8192,
        "matrixSize": 4096,
        "precision": "FP16",
        "warmupSeconds": 3,
        "measurementSeconds": 20,
        "iterations": 123,
        "elapsedMs": 20031,
        "throughputTflops": 12.34,
        "benchmarkScore": 12.34,
        "measuredAt": "2026-09-01T00:00:00Z",
    }
    value.update(overrides)
    return value


def gpu_state() -> WorkerGpuState:
    return WorkerGpuState(
        gpu_uuid="GPU-test-uuid",
        gpu_model="NVIDIA Test GPU",
        total_vram_mb=8192,
        free_vram_mb=7800,
        gpu_utilization=0,
        cuda_available=True,
    )


class BenchmarkResultTest(unittest.TestCase):
    def test_parses_valid_result_json(self) -> None:
        result = BenchmarkResult.from_mapping(json.loads(json.dumps(payload())))

        self.assertEqual(result.benchmark_version, "gpu-benchmark-v1")
        self.assertEqual(result.benchmark_score, 12.34)
        self.assertEqual(result.iterations, 123)

    def test_non_positive_score_is_invalid(self) -> None:
        with self.assertRaisesRegex(BenchmarkError, "benchmarkScore"):
            BenchmarkResult.from_mapping(payload(benchmarkScore=0))

    def test_nan_and_infinity_are_invalid(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                with self.assertRaises(BenchmarkError):
                    BenchmarkResult.from_mapping(
                        payload(benchmarkScore=value, throughputTflops=value)
                    )


class BenchmarkCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.cache = BenchmarkCache(self.directory, logger=lambda _message: None)

    def _write(self, value: object) -> None:
        self.cache.path.write_text(json.dumps(value), encoding="utf-8")

    def test_same_uuid_and_version_returns_cache_hit(self) -> None:
        self._write(payload())

        result = self.cache.load(gpu_uuid="GPU-test-uuid")

        self.assertIsNotNone(result)
        self.assertEqual(result.benchmark_score, 12.34)

    def test_changed_gpu_uuid_returns_cache_miss(self) -> None:
        self._write(payload())

        self.assertIsNone(self.cache.load(gpu_uuid="GPU-different"))

    def test_changed_benchmark_version_returns_cache_miss(self) -> None:
        self._write(payload(benchmarkVersion="gpu-benchmark-v0"))

        self.assertIsNone(self.cache.load(gpu_uuid="GPU-test-uuid"))

    def test_corrupted_cache_returns_cache_miss(self) -> None:
        self.cache.path.write_text("{", encoding="utf-8")

        self.assertIsNone(self.cache.load(gpu_uuid="GPU-test-uuid"))


class BenchmarkRunnerTest(unittest.IsolatedAsyncioTestCase):
    async def test_success_saves_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = BenchmarkCache(Path(directory), logger=lambda _message: None)
            docker = AsyncMock()
            container_payload = payload()
            for key in ("gpuUuid", "gpuModel", "vramMb", "measuredAt"):
                container_payload.pop(key)
            docker.execute_benchmark.return_value = DockerExecutionResult(
                False, container_payload, 0
            )
            runner = BenchmarkRunner(
                docker,
                cache,
                gpu_index=0,
                clock=lambda: datetime(2026, 9, 1, tzinfo=timezone.utc),
                logger=lambda _message: None,
            )

            result = await runner.ensure(gpu_state())

            self.assertEqual(result.benchmark_score, 12.34)
            self.assertTrue(cache.path.is_file())
            self.assertEqual(
                json.loads(cache.path.read_text(encoding="utf-8"))["gpuUuid"],
                "GPU-test-uuid",
            )

    async def test_failure_is_fatal_and_not_cached(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = BenchmarkCache(Path(directory), logger=lambda _message: None)
            docker = AsyncMock()
            docker.execute_benchmark.side_effect = DockerManagerError(
                "BENCHMARK_TIMEOUT", "timed out"
            )
            runner = BenchmarkRunner(
                docker, cache, gpu_index=0, logger=lambda _message: None
            )

            with self.assertRaisesRegex(BenchmarkError, "BENCHMARK_TIMEOUT"):
                await runner.ensure(gpu_state())

            self.assertFalse(cache.path.exists())

    async def test_cache_hit_does_not_execute_docker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = BenchmarkCache(Path(directory), logger=lambda _message: None)
            cache.save(BenchmarkResult.from_mapping(payload()))
            docker = AsyncMock()
            runner = BenchmarkRunner(
                docker, cache, gpu_index=0, logger=lambda _message: None
            )

            result = await runner.ensure(gpu_state())

            self.assertEqual(result.benchmark_score, 12.34)
            docker.execute_benchmark.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
