from types import SimpleNamespace
import unittest

from gpu_worker.errors import GpuInspectionError
from gpu_worker.gpu import NvmlGpuProbe


class FakeNvml:
    def __init__(self, count=1):
        self.count = count
        self.initialized = False
        self.shutdown_called = False

    def nvmlInit(self):
        self.initialized = True

    def nvmlShutdown(self):
        self.shutdown_called = True

    def nvmlDeviceGetCount(self):
        return self.count

    def nvmlDeviceGetHandleByIndex(self, index):
        return f"handle-{index}"

    def nvmlDeviceGetName(self, handle):
        return b"NVIDIA Test GPU"

    def nvmlDeviceGetMemoryInfo(self, handle):
        mib = 1024 * 1024
        return SimpleNamespace(total=8192 * mib, free=7100 * mib)

    def nvmlDeviceGetUtilizationRates(self, handle):
        return SimpleNamespace(gpu=37)


class NvmlGpuProbeTests(unittest.TestCase):
    def test_reads_gpu_snapshot_and_shuts_down(self):
        nvml = FakeNvml()

        with NvmlGpuProbe(index=0, nvml_module=nvml) as probe:
            snapshot = probe.snapshot()

        self.assertEqual(snapshot.name, "NVIDIA Test GPU")
        self.assertEqual(snapshot.total_vram_mb, 8192)
        self.assertEqual(snapshot.free_vram_mb, 7100)
        self.assertEqual(snapshot.utilization_percent, 37)
        self.assertTrue(nvml.shutdown_called)

    def test_rejects_invalid_gpu_index(self):
        nvml = FakeNvml(count=1)

        with self.assertRaisesRegex(GpuInspectionError, "invalid"):
            with NvmlGpuProbe(index=1, nvml_module=nvml):
                pass

        self.assertTrue(nvml.shutdown_called)


if __name__ == "__main__":
    unittest.main()

