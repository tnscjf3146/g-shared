import re
import unittest

from gpu_worker.errors import WorkerConfigurationError
from gpu_worker.workloads import (
    GPU_STANDARD_BENCHMARK,
    get_gpu_probe_image,
    get_system_workload_spec,
    get_workload_spec,
    supported_workloads,
)


class WorkloadCatalogTest(unittest.TestCase):
    def test_gpu_pytorch_test_uses_repository_at_full_sha256_digest(self):
        spec = get_workload_spec("GPU_PYTORCH_TEST")

        self.assertRegex(
            spec.image,
            r"^le3560/gpu-pytorch-poc@sha256:[0-9a-f]{64}$",
        )
        self.assertGreater(spec.timeout_seconds, 0)
        self.assertGreater(spec.max_output_bytes, 0)
        self.assertFalse(spec.execution_proof)

    def test_30s_workload_uses_immutable_dedicated_image_and_timeout(self):
        spec = get_workload_spec("GPU_PYTORCH_TEST_30S")

        self.assertEqual(
            spec.image,
            "le3560/gpu-pytorch-poc-30s@sha256:"
            "d7fe2de03ff8532900c600d9620cd7d57fcedbeb6531b3acc2b53a9f09a71a49",
        )
        self.assertEqual(spec.timeout_seconds, 90)
        self.assertTrue(spec.execution_proof)

    def test_unknown_workload_is_rejected(self):
        with self.assertRaisesRegex(WorkerConfigurationError, "not allowlisted"):
            get_workload_spec("USER_CONTROLLED_IMAGE")

    def test_standard_benchmark_is_internal_and_uses_a_pinned_runtime(self):
        spec = get_system_workload_spec(GPU_STANDARD_BENCHMARK)

        self.assertRegex(spec.image, r"^[^\s]+@sha256:[0-9a-f]{64}$")
        self.assertEqual(spec.timeout_seconds, 90)
        self.assertNotIn(GPU_STANDARD_BENCHMARK, supported_workloads())
        with self.assertRaisesRegex(WorkerConfigurationError, "not allowlisted"):
            get_workload_spec(GPU_STANDARD_BENCHMARK)

    def test_catalog_does_not_accept_backend_image_override(self):
        with self.assertRaises(TypeError):
            get_workload_spec(
                "GPU_PYTORCH_TEST",
                image="attacker/image:latest",
            )

    def test_gpu_probe_image_is_fixed_by_digest(self):
        self.assertRegex(
            get_gpu_probe_image(),
            r"^nvidia/cuda@sha256:[0-9a-f]{64}$",
        )


if __name__ == "__main__":
    unittest.main()
