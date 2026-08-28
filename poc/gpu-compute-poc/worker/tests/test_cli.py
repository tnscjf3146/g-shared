from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from gpu_worker.cli import main
from gpu_worker.embedding import EmbeddingRunResult


class FakeCuda:
    def __init__(self, available):
        self._available = available

    def is_available(self):
        return self._available


class FakeProbe:
    def __init__(self, index=0):
        self.index = index

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def snapshot(self):
        return SimpleNamespace(
            name="NVIDIA Test GPU",
            total_vram_mb=8192,
            free_vram_mb=7000,
            utilization_percent=4,
        )


class FakeSampler:
    def __init__(self, probe):
        self.probe = probe

    def start(self):
        return None

    def stop(self):
        return 91


def fake_result(device):
    return EmbeddingRunResult(
        model_name="BAAI/bge-small-en-v1.5",
        device=device,
        input_count=3,
        embedding_shape=(3, 384),
        model_load_ms=100.0,
        runtime_ms=20.0,
    )


class CliTests(unittest.TestCase):
    @patch("gpu_worker.cli.run_embedding", return_value=fake_result("cuda:0"))
    @patch("gpu_worker.cli.GpuUtilizationSampler", FakeSampler)
    @patch("gpu_worker.cli.NvmlGpuProbe", FakeProbe)
    @patch(
        "gpu_worker.cli.load_torch",
        return_value=SimpleNamespace(cuda=FakeCuda(True), version=SimpleNamespace(cuda="12.6")),
    )
    def test_cuda_success_output(self, _load_torch, run_embedding_mock):
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(["--repeat", "1"])

        self.assertEqual(exit_code, 0)
        self.assertIn("device=cuda", output.getvalue())
        self.assertIn("Peak GPU utilization: 91%", output.getvalue())
        self.assertIn("POC SUCCESS: YES", output.getvalue())
        self.assertEqual(run_embedding_mock.call_args.kwargs["device"], "cuda")

    @patch(
        "gpu_worker.cli.load_torch",
        return_value=SimpleNamespace(cuda=FakeCuda(False), version=SimpleNamespace(cuda=None)),
    )
    def test_cuda_unavailable_fails_without_fallback(self, _load_torch):
        errors = StringIO()

        with redirect_stderr(errors):
            exit_code = main(["--repeat", "1"])

        self.assertEqual(exit_code, 2)
        self.assertIn("CUDA unavailable", errors.getvalue())
        self.assertIn("POC SUCCESS: NO", errors.getvalue())

    @patch("gpu_worker.cli.run_embedding", return_value=fake_result("cpu"))
    @patch(
        "gpu_worker.cli.load_torch",
        return_value=SimpleNamespace(cuda=FakeCuda(False), version=SimpleNamespace(cuda=None)),
    )
    def test_explicit_cpu_fallback_never_passes_poc(self, _load_torch, _run_embedding):
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(["--allow-cpu", "--repeat", "1"])

        self.assertEqual(exit_code, 3)
        self.assertIn("device=cpu", output.getvalue())
        self.assertIn("POC SUCCESS: NO", output.getvalue())


if __name__ == "__main__":
    unittest.main()
