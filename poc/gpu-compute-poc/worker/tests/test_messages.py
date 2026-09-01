"""Tests for REGISTER and HEARTBEAT payload construction."""

from __future__ import annotations

import unittest

from gpu_worker.messages import NvmlWorkerMessageSource, WorkerMetadata
from gpu_worker.benchmark_models import BenchmarkResult
from gpu_worker.runtime import WorkerGpuState


class SequenceProvider:
    def __init__(self, states: list[WorkerGpuState]) -> None:
        self._states = iter(states)
        self.calls = 0

    def snapshot(self) -> WorkerGpuState:
        self.calls += 1
        return next(self._states)


class WorkerMessageSourceTests(unittest.TestCase):
    def test_register_contains_configured_metadata_and_live_gpu_snapshot(self) -> None:
        provider = SequenceProvider([state(7800, 4)])
        source = NvmlWorkerMessageSource(provider, metadata())

        payload = source.register_payload()

        self.assertEqual(
            payload,
            {
                "type": "REGISTER",
                "workerId": "worker-001",
                "gpuModel": "NVIDIA Test GPU",
                "totalVramMb": 8192,
                "freeVramMb": 7800,
                "gpuUtilization": 4,
                "cudaAvailable": True,
                "supportedWorkloads": ["EMBEDDING"],
                "hourlyPrice": 600,
                "workerStatus": "IDLE",
            },
        )

    def test_heartbeat_reads_a_fresh_snapshot(self) -> None:
        provider = SequenceProvider([state(7800, 4), state(7600, 84)])
        source = NvmlWorkerMessageSource(provider, metadata())
        source.register_payload()

        payload = source.heartbeat_payload()

        self.assertEqual(
            payload,
            {
                "type": "HEARTBEAT",
                "workerId": "worker-001",
                "freeVramMb": 7600,
                "gpuUtilization": 84,
                "workerStatus": "IDLE",
            },
        )
        self.assertEqual(provider.calls, 2)

    def test_register_contains_numeric_benchmark_payload(self) -> None:
        provider = SequenceProvider([state(7800, 4)])
        benchmark = BenchmarkResult.from_mapping({
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
        })
        source = NvmlWorkerMessageSource(provider, metadata(), benchmark=benchmark)

        result = source.register_payload()["benchmark"]

        self.assertEqual(result["version"], "gpu-benchmark-v1")
        self.assertIsInstance(result["score"], float)
        self.assertEqual(result["score"], 12.34)
        self.assertEqual(result["matrixSize"], 4096)


def state(free_vram_mb: int, utilization: int) -> WorkerGpuState:
    return WorkerGpuState(
        gpu_uuid="GPU-test-uuid",
        gpu_model="NVIDIA Test GPU",
        total_vram_mb=8192,
        free_vram_mb=free_vram_mb,
        gpu_utilization=utilization,
        cuda_available=True,
    )


def metadata() -> WorkerMetadata:
    return WorkerMetadata(
        worker_id="worker-001",
        hourly_price=600,
        supported_workloads=("EMBEDDING",),
    )


if __name__ == "__main__":
    unittest.main()
