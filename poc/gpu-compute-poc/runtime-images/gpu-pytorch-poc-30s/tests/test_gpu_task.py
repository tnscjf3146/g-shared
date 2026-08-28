import io
import json
import unittest
from contextlib import redirect_stdout

import gpu_task


class MatrixSizingTests(unittest.TestCase):
    def test_reuses_safe_vram_budget_and_caps_matrix_at_8192(self):
        self.assertEqual(gpu_task.select_matrix_size(8 * 1024**3), 8192)

        size = gpu_task.select_matrix_size(1024**3)
        self.assertLessEqual(3 * size * size * 4, int(1024**3 * 0.20))
        self.assertGreaterEqual(size, 2048)

    def test_rejects_vram_that_cannot_fit_minimum_matrix(self):
        with self.assertRaises(gpu_task.GpuTaskError) as context:
            gpu_task.select_matrix_size(128 * 1024**2)

        self.assertEqual(context.exception.error_code, "INSUFFICIENT_VRAM")


class _FakeClock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        current = self.value
        self.value += 10.0
        return current


class _FakeScalar:
    def __init__(self, value):
        self.value = value

    def item(self):
        return self.value


class _FakeResult:
    def __init__(self, matrix_size):
        self.matrix_size = matrix_size

    def __getitem__(self, _key):
        return _FakeScalar(float(self.matrix_size))


class _FakeTensor:
    def __init__(self, matrix_size):
        self.matrix_size = matrix_size

    def __matmul__(self, _other):
        return _FakeResult(self.matrix_size)


class _FakeCuda:
    @staticmethod
    def is_available():
        return True

    @staticmethod
    def mem_get_info():
        return 8 * 1024**3, 8 * 1024**3

    @staticmethod
    def get_device_name(_index):
        return "Fake NVIDIA GPU"

    @staticmethod
    def synchronize():
        return None


class _FakeTorch:
    cuda = _FakeCuda()
    float32 = object()

    @staticmethod
    def ones(shape, **_kwargs):
        return _FakeTensor(shape[0])


class ResultContractTests(unittest.TestCase):
    def test_elapsed_time_loop_returns_traceable_cuda_result(self):
        result = gpu_task.run_cuda_task(
            _FakeTorch(),
            job_id="job-001",
            target_duration_seconds=30,
            clock=_FakeClock(),
        )

        self.assertEqual(result.job_id, "job-001")
        self.assertGreater(result.iterations, 0)
        self.assertEqual(result.matrix_size, 8192)
        self.assertEqual(result.target_duration_seconds, 30)
        self.assertEqual(result.runtime_ms, 30000)
        self.assertEqual(len(result.result_checksum), 64)

    def test_success_payload_contains_execution_proof_fields(self):
        result = gpu_task.TaskResult(
            job_id="job-001",
            gpu_model="NVIDIA GeForce RTX 4070 Laptop GPU",
            runtime_ms=30001,
            result_checksum="a" * 64,
            matrix_size=8192,
            iterations=42,
            target_duration_seconds=30,
        )

        payload = result.to_payload()
        self.assertEqual(payload["jobId"], "job-001")
        self.assertEqual(payload["iterations"], 42)
        self.assertEqual(payload["matrixSize"], 8192)
        self.assertEqual(payload["targetDurationSeconds"], 30)
        self.assertEqual(payload["runtimeMs"], 30001)
        self.assertEqual(payload["resultChecksum"], "a" * 64)

    def test_main_rejects_missing_job_id(self):
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = gpu_task.main(torch_module=_FakeTorch(), environ={})

        marker, payload_json = output.getvalue().strip().split("=", 1)
        payload = json.loads(payload_json)
        self.assertEqual(marker, "GPU_TASK_RESULT")
        self.assertEqual(exit_code, 20)
        self.assertEqual(payload["status"], "FAILED")
        self.assertEqual(payload["errorCode"], "JOB_ID_MISSING")


if __name__ == "__main__":
    unittest.main()
