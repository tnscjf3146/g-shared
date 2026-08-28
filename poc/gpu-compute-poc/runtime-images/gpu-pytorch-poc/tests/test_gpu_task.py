import io
import json
import unittest
from contextlib import redirect_stdout

import gpu_task


class MatrixSizingTests(unittest.TestCase):
    def test_caps_matrix_at_8192(self):
        self.assertEqual(gpu_task.select_matrix_size(8 * 1024**3), 8192)

    def test_selected_matrix_stays_within_twenty_percent_budget(self):
        free_bytes = 1024**3
        size = gpu_task.select_matrix_size(free_bytes)

        required_bytes = 3 * size * size * 4
        self.assertLessEqual(required_bytes, int(free_bytes * 0.20))
        self.assertGreaterEqual(size, 2048)

    def test_rejects_vram_that_cannot_fit_minimum_matrix(self):
        with self.assertRaises(gpu_task.GpuTaskError) as context:
            gpu_task.select_matrix_size(128 * 1024**2)

        self.assertEqual(context.exception.error_code, "INSUFFICIENT_VRAM")


class ResultContractTests(unittest.TestCase):
    def test_success_payload_has_required_result_contract(self):
        result = gpu_task.TaskResult(
            gpu_model="NVIDIA GeForce RTX 4070 Laptop GPU",
            runtime_ms=850,
            result_checksum="a" * 64,
            matrix_size=8192,
            iterations=8,
        )

        self.assertEqual(
            result.to_payload(),
            {
                "schemaVersion": 1,
                "status": "SUCCESS",
                "cudaAvailable": True,
                "gpuModel": "NVIDIA GeForce RTX 4070 Laptop GPU",
                "runtimeMs": 850,
                "resultChecksum": "a" * 64,
                "matrixSize": 8192,
                "iterations": 8,
            },
        )

    def test_main_emits_structured_cuda_unavailable_failure(self):
        class UnavailableCuda:
            @staticmethod
            def is_available():
                return False

        class FakeTorch:
            cuda = UnavailableCuda()

        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = gpu_task.main(torch_module=FakeTorch())

        marker, payload_json = output.getvalue().strip().split("=", 1)
        payload = json.loads(payload_json)
        self.assertEqual(marker, "GPU_TASK_RESULT")
        self.assertEqual(exit_code, 20)
        self.assertEqual(payload["status"], "FAILED")
        self.assertEqual(payload["errorCode"], "CUDA_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
