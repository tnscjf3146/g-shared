from types import SimpleNamespace
import unittest

from gpu_worker.embedding import run_embedding
from gpu_worker.errors import CudaUnavailableError, DeviceVerificationError


class FakeCuda:
    def __init__(self, available=True):
        self.available = available
        self.synchronize_calls = 0

    def is_available(self):
        return self.available

    def synchronize(self):
        self.synchronize_calls += 1


class FakeEmbeddings:
    def __init__(self, shape):
        self.shape = shape


class FakeModel:
    def __init__(self, device="cuda:0"):
        self.device = device
        self.encode_arguments = None

    def encode(self, inputs, **kwargs):
        self.encode_arguments = (inputs, kwargs)
        return FakeEmbeddings((len(inputs), 384))


class EmbeddingRuntimeTests(unittest.TestCase):
    def test_cuda_is_explicit_and_timing_is_synchronized(self):
        cuda = FakeCuda(available=True)
        torch_module = SimpleNamespace(cuda=cuda)
        model = FakeModel("cuda:0")
        factory_calls = []
        ticks = iter((10.0, 10.25, 20.0, 21.2))

        def factory(model_name, device):
            factory_calls.append((model_name, device))
            return model

        result = run_embedding(
            "BAAI/bge-small-en-v1.5",
            ["a", "b"],
            device="cuda",
            batch_size=8,
            torch_module=torch_module,
            model_factory=factory,
            clock=lambda: next(ticks),
        )

        self.assertEqual(factory_calls, [("BAAI/bge-small-en-v1.5", "cuda")])
        self.assertEqual(model.encode_arguments[1]["device"], "cuda")
        self.assertEqual(model.encode_arguments[1]["batch_size"], 8)
        self.assertEqual(cuda.synchronize_calls, 2)
        self.assertEqual(result.device, "cuda:0")
        self.assertEqual(result.embedding_shape, (2, 384))
        self.assertAlmostEqual(result.model_load_ms, 250.0)
        self.assertAlmostEqual(result.runtime_ms, 1200.0)

    def test_rejects_cuda_when_torch_reports_unavailable(self):
        torch_module = SimpleNamespace(cuda=FakeCuda(available=False))

        with self.assertRaises(CudaUnavailableError):
            run_embedding(
                "BAAI/bge-small-en-v1.5",
                ["test"],
                device="cuda",
                torch_module=torch_module,
                model_factory=lambda *_: FakeModel(),
            )

    def test_rejects_model_that_is_not_on_cuda(self):
        torch_module = SimpleNamespace(cuda=FakeCuda(available=True))

        with self.assertRaises(DeviceVerificationError):
            run_embedding(
                "BAAI/bge-small-en-v1.5",
                ["test"],
                device="cuda",
                torch_module=torch_module,
                model_factory=lambda *_: FakeModel("cpu"),
            )

    def test_rejects_unapproved_model(self):
        with self.assertRaisesRegex(ValueError, "not approved"):
            run_embedding(
                "untrusted/arbitrary-model",
                ["test"],
                torch_module=SimpleNamespace(cuda=FakeCuda()),
                model_factory=lambda *_: FakeModel(),
            )


if __name__ == "__main__":
    unittest.main()

