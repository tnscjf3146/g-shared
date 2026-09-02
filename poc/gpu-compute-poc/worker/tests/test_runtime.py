"""Tests for the CUDA/NVML status provider used by STEP 2."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from gpu_worker.errors import CudaUnavailableError, GpuInspectionError
from gpu_worker.gpu import GpuSnapshot
from gpu_worker.runtime import CudaGpuStatusProvider, WorkerGpuState


class FakeProbe:
    def __init__(self, index: int, snapshot: GpuSnapshot) -> None:
        self.index = index
        self._snapshot = snapshot
        self.entered = False
        self.exited = False

    def __enter__(self) -> "FakeProbe":
        self.entered = True
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.exited = True

    def snapshot(self) -> GpuSnapshot:
        return self._snapshot


class CudaGpuStatusProviderTests(unittest.TestCase):
    def test_cuda_unavailable_fails_before_nvml_probe_is_created(self) -> None:
        probe_created = False

        def probe_factory(index: int):
            nonlocal probe_created
            probe_created = True
            raise AssertionError("NVML must not open")

        torch_module = SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: False)
        )
        provider = CudaGpuStatusProvider(
            0,
            torch_loader=lambda: torch_module,
            probe_factory=probe_factory,
        )

        with self.assertRaises(CudaUnavailableError):
            provider.__enter__()

        self.assertFalse(probe_created)

    def test_snapshot_maps_live_nvml_values_and_closes_probe(self) -> None:
        torch_module = SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: True)
        )
        probe = FakeProbe(
            0,
            GpuSnapshot(
                index=0,
                uuid="GPU-test-uuid",
                name="NVIDIA Test GPU",
                total_vram_mb=8192,
                free_vram_mb=7800,
                utilization_percent=7,
            ),
        )
        provider = CudaGpuStatusProvider(
            0,
            torch_loader=lambda: torch_module,
            probe_factory=lambda index: probe,
        )

        with provider:
            state = provider.snapshot()

        self.assertEqual(
            state,
            WorkerGpuState(
                gpu_uuid="GPU-test-uuid",
                gpu_model="NVIDIA Test GPU",
                total_vram_mb=8192,
                free_vram_mb=7800,
                gpu_utilization=7,
                cuda_available=True,
            ),
        )
        self.assertTrue(probe.entered)
        self.assertTrue(probe.exited)

    def test_snapshot_reopens_nvml_once_when_persistent_handle_fails(self) -> None:
        torch_module = SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: True)
        )
        expected = GpuSnapshot(
            index=0,
            uuid="GPU-test-uuid",
            name="NVIDIA Test GPU",
            total_vram_mb=8192,
            free_vram_mb=7700,
            utilization_percent=9,
        )

        class StaleProbe(FakeProbe):
            def __init__(self) -> None:
                super().__init__(0, expected)
                self.calls = 0

            def snapshot(self) -> GpuSnapshot:
                self.calls += 1
                if self.calls == 2:
                    raise GpuInspectionError("NVML metric read failed: Unknown Error")
                return expected

        stale_probe = StaleProbe()
        replacement_probe = FakeProbe(0, expected)
        probes = iter((stale_probe, replacement_probe))
        provider = CudaGpuStatusProvider(
            0,
            torch_loader=lambda: torch_module,
            probe_factory=lambda index: next(probes),
        )

        with provider:
            first = provider.snapshot()
            second = provider.snapshot()

        self.assertEqual(first.free_vram_mb, 7700)
        self.assertEqual(second.free_vram_mb, 7700)
        self.assertTrue(stale_probe.exited)
        self.assertTrue(replacement_probe.entered)
        self.assertTrue(replacement_probe.exited)


if __name__ == "__main__":
    unittest.main()
