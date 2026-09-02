from __future__ import annotations

import unittest

from gpu_worker.benchmark_program import BENCHMARK_PROGRAM


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        value = self.value
        self.value += 1.0
        return value


class _Scalar:
    def item(self) -> float:
        return 4096.0


class _Result:
    def __getitem__(self, _key):
        return _Scalar()


class _Tensor:
    def __matmul__(self, _other):
        return _Result()


class _Cuda:
    synchronized = 0

    @staticmethod
    def is_available() -> bool:
        return True

    @staticmethod
    def get_device_name(_index: int) -> str:
        return "Fake NVIDIA GPU"

    @staticmethod
    def get_device_properties(_index: int):
        return type("Properties", (), {"total_memory": 8 * 1024**3})()

    @classmethod
    def synchronize(cls) -> None:
        cls.synchronized += 1


class _Torch:
    cuda = _Cuda()
    float16 = object()

    @staticmethod
    def ones(_shape, **_kwargs):
        return _Tensor()


def _program_namespace() -> dict[str, object]:
    namespace: dict[str, object] = {"__name__": "benchmark_test"}
    exec(compile(BENCHMARK_PROGRAM, "<test-benchmark>", "exec"), namespace)
    return namespace


class BenchmarkProgramTest(unittest.TestCase):
    def test_v1_uses_fixed_fp16_shape_and_real_timed_matmuls(self) -> None:
        namespace = _program_namespace()

        result = namespace["run_benchmark"](_Torch(), clock=_Clock())

        self.assertEqual(result["benchmarkVersion"], "gpu-benchmark-v1")
        self.assertEqual(result["matrixSize"], 4096)
        self.assertEqual(result["precision"], "FP16")
        self.assertEqual(result["warmupSeconds"], 3)
        self.assertEqual(result["measurementSeconds"], 20)
        self.assertGreater(result["iterations"], 0)
        self.assertGreaterEqual(result["elapsedMs"], 20_000)
        self.assertGreater(result["throughputTflops"], 0)
        self.assertEqual(result["benchmarkScore"], result["throughputTflops"])
        self.assertGreater(_Cuda.synchronized, result["iterations"])

    def test_cuda_unavailable_has_benchmark_specific_error(self) -> None:
        namespace = _program_namespace()
        cuda = type("Cuda", (), {"is_available": staticmethod(lambda: False)})()
        torch_module = type("Torch", (), {"cuda": cuda})()

        with self.assertRaisesRegex(namespace["BenchmarkProgramError"], "is_available") as raised:
            namespace["run_benchmark"](torch_module)

        self.assertEqual(raised.exception.code, "BENCHMARK_CUDA_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
