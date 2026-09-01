"""Tests for STEP 2 Worker client configuration."""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

from gpu_worker.client import config_from_args, main
from gpu_worker.exit_codes import WorkerExitCode
from gpu_worker.errors import BenchmarkError, CudaUnavailableError, WorkerConfigurationError
from gpu_worker.runtime import WorkerGpuState
from gpu_worker.transport import WorkerClientConfig


class WorkerClientConfigTests(unittest.TestCase):
    def test_cli_builds_approved_default_configuration(self) -> None:
        config = config_from_args(
            [
                "--backend-url",
                "ws://127.0.0.1:8080/ws/workers",
                "--worker-id",
                "worker-001",
                "--hourly-price",
                "600",
            ]
        )

        self.assertEqual(config.worker_id, "worker-001")
        self.assertEqual(config.hourly_price, 600)
        self.assertEqual(config.gpu_index, 0)
        self.assertEqual(config.supported_workloads, ("EMBEDDING",))
        self.assertEqual(config.heartbeat_interval_seconds, 5.0)

    def test_configuration_rejects_invalid_values(self) -> None:
        valid = {
            "backend_url": "ws://127.0.0.1:8080/ws/workers",
            "worker_id": "worker-001",
            "hourly_price": 600,
            "gpu_index": 0,
            "supported_workloads": ("EMBEDDING",),
        }
        invalid = (
            {**valid, "backend_url": "http://127.0.0.1"},
            {**valid, "worker_id": " "},
            {**valid, "hourly_price": -1},
            {**valid, "gpu_index": -1},
            {**valid, "supported_workloads": ()},
            {**valid, "supported_workloads": ("EMBEDDING", " ")},
            {**valid, "heartbeat_interval_seconds": 0},
        )

        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(WorkerConfigurationError):
                    WorkerClientConfig(**values)

    def test_cuda_failure_prevents_websocket_connection(self) -> None:
        class UnavailableProvider:
            def __init__(self, index: int) -> None:
                self.index = index

            def __enter__(self):
                raise CudaUnavailableError("CUDA unavailable")

            def __exit__(self, exc_type, exc, traceback) -> None:
                return None

        runner = AsyncMock()
        stderr = StringIO()

        with redirect_stderr(stderr):
            exit_code = main(
                [
                    "--backend-url",
                    "ws://127.0.0.1:8080/ws/workers",
                    "--worker-id",
                    "worker-001",
                    "--hourly-price",
                    "600",
                ],
                provider_factory=UnavailableProvider,
                connection_runner=runner,
            )

        self.assertEqual(exit_code, 2)
        self.assertIn("CUDA unavailable", stderr.getvalue())
        runner.assert_not_awaited()

    def test_an_already_running_worker_exits_before_any_gpu_or_register_work(self) -> None:
        class _RefusedGuard:
            issue_code = "WORKER_ALREADY_RUNNING"
            acquired = False
            released = False

            def acquire(self) -> bool:
                return False

            def release(self) -> None:
                type(self).released = True

        provider_factory = AsyncMock()
        docker_factory = AsyncMock()
        runner = AsyncMock()
        stderr = StringIO()

        with redirect_stderr(stderr):
            exit_code = main(
                [
                    "--backend-url", "ws://127.0.0.1:8080/ws/workers",
                    "--worker-id", "worker-001",
                    "--hourly-price", "600",
                ],
                provider_factory=provider_factory,
                docker_factory=docker_factory,
                connection_runner=runner,
                guard_factory=lambda worker_id, gpu_index: _RefusedGuard(),
            )

        self.assertEqual(exit_code, WorkerExitCode.ALREADY_RUNNING)
        self.assertEqual(exit_code, 5)
        runner.assert_not_awaited()
        provider_factory.assert_not_called()
        docker_factory.assert_not_called()
        self.assertIn("WORKER_ALREADY_RUNNING", stderr.getvalue())

    def test_the_already_running_exit_is_not_an_installation_failure(self) -> None:
        self.assertFalse(WorkerExitCode.ALREADY_RUNNING.is_install_failure)
        self.assertFalse(WorkerExitCode.OK.is_install_failure)
        self.assertTrue(WorkerExitCode.EXPECTED_FAILURE.is_install_failure)
        self.assertTrue(WorkerExitCode.TRANSPORT_FAILURE.is_install_failure)
        self.assertTrue(WorkerExitCode.CREDENTIAL_MISMATCH.is_install_failure)

    def test_the_guard_is_built_from_the_worker_id_and_gpu_index_only(self) -> None:
        seen: list[tuple[object, ...]] = []

        class _Guard:
            issue_code = None
            acquired = False

            def acquire(self) -> bool:
                return False

            def release(self) -> None:
                return None

        def guard_factory(worker_id, gpu_index):
            seen.append((worker_id, gpu_index))
            return _Guard()

        with redirect_stderr(StringIO()):
            main(
                [
                    "--backend-url", "ws://127.0.0.1:8080/ws/workers",
                    "--worker-id", "worker-001",
                    "--hourly-price", "600",
                    "--gpu-index", "2",
                ],
                provider_factory=AsyncMock(),
                connection_runner=AsyncMock(),
                guard_factory=guard_factory,
            )

        self.assertEqual(seen, [("worker-001", 2)])

    def test_benchmark_failure_prevents_register_connection(self) -> None:
        state = WorkerGpuState(
            gpu_uuid="GPU-test-uuid",
            gpu_model="NVIDIA Test GPU",
            total_vram_mb=8192,
            free_vram_mb=7800,
            gpu_utilization=0,
            cuda_available=True,
        )

        class ReadyProvider:
            def __init__(self, _index: int) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback) -> None:
                return None

            def snapshot(self) -> WorkerGpuState:
                return state

        class FailingBenchmarkRunner:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            async def ensure(self, _state: WorkerGpuState):
                raise BenchmarkError("BENCHMARK_FAILED", "benchmark failed")

        docker = AsyncMock()
        docker.check_environment.return_value = None
        connection = AsyncMock()
        stderr = StringIO()

        with redirect_stderr(stderr):
            exit_code = main(
                [
                    "--backend-url", "ws://127.0.0.1:8080/ws/workers",
                    "--worker-id", "worker-001",
                    "--hourly-price", "600",
                ],
                provider_factory=ReadyProvider,
                docker_factory=lambda: docker,
                benchmark_cache_factory=lambda **_kwargs: object(),
                benchmark_runner_factory=FailingBenchmarkRunner,
                connection_runner=connection,
            )

        self.assertEqual(exit_code, 2)
        self.assertIn("benchmark failed", stderr.getvalue())
        connection.assert_not_awaited()


class AgentConfigFileClientTests(unittest.TestCase):
    """Double-click execution reads worker-config.json next to the executable."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.directory = Path(self._tmp.name)

    def _write_config(self, payload: dict[str, object]) -> None:
        (self.directory / "worker-config.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def test_no_cli_arguments_uses_config_file_next_to_executable(self) -> None:
        self._write_config(
            {
                "backendUrl": "wss://example.com/ws/workers",
                "workerId": "worker-001",
                "workerToken": "file-token",
                "hourlyPrice": 600,
                "gpuIndex": 0,
                "supportedWorkloads": ["GPU_PYTORCH_TEST"],
            }
        )

        config = config_from_args([], env={}, directory=self.directory)

        self.assertEqual(config.backend_url, "wss://example.com/ws/workers")
        self.assertEqual(config.worker_id, "worker-001")
        self.assertEqual(config.worker_token, "file-token")
        self.assertEqual(config.hourly_price, 600)
        self.assertEqual(config.gpu_index, 0)
        self.assertEqual(config.supported_workloads, ("GPU_PYTORCH_TEST",))
        self.assertEqual(config.heartbeat_interval_seconds, 5.0)

    def test_cli_arguments_still_override_config_file(self) -> None:
        self._write_config(
            {
                "backendUrl": "wss://file/ws/workers",
                "workerId": "file-worker",
                "hourlyPrice": 100,
                "supportedWorkloads": ["EMBEDDING"],
            }
        )

        config = config_from_args(
            [
                "--backend-url",
                "ws://127.0.0.1:8080/ws/workers",
                "--worker-id",
                "worker-001",
                "--hourly-price",
                "600",
                "--supported-workload",
                "GPU_PYTORCH_TEST",
            ],
            env={},
            directory=self.directory,
        )

        self.assertEqual(config.backend_url, "ws://127.0.0.1:8080/ws/workers")
        self.assertEqual(config.worker_id, "worker-001")
        self.assertEqual(config.hourly_price, 600)
        self.assertEqual(config.supported_workloads, ("GPU_PYTORCH_TEST",))

    def test_token_environment_variable_is_still_supported(self) -> None:
        self._write_config(
            {
                "backendUrl": "wss://example.com/ws/workers",
                "workerId": "worker-001",
                "workerToken": "file-token",
                "hourlyPrice": 600,
            }
        )

        config = config_from_args(
            [], env={"GPU_WORKER_TOKEN": "env-token"}, directory=self.directory
        )

        self.assertEqual(config.worker_token, "env-token")

    def test_protected_config_is_resolved_before_client_construction(self) -> None:
        self._write_config(
            {
                "backendUrl": "wss://example.com/ws/workers",
                "workerId": "worker-001",
                "workerTokenProtected": "encoded-value",
                "hourlyPrice": 600,
            }
        )

        with patch(
            "gpu_worker.agent_config.unprotect_current_user",
            return_value="protected-value",
        ):
            config = config_from_args([], env={}, directory=self.directory)

        self.assertEqual(config.worker_token, "protected-value")

    def test_missing_configuration_prints_readable_error_without_traceback(
        self,
    ) -> None:
        runner = AsyncMock()
        provider_factory = AsyncMock()
        stderr = StringIO()

        with redirect_stderr(stderr):
            exit_code = main(
                [],
                env={},
                directory=self.directory,
                provider_factory=provider_factory,
                connection_runner=runner,
            )

        message = stderr.getvalue()
        self.assertEqual(exit_code, 2)
        self.assertIn("worker-config.json", message)
        self.assertIn("worker-config.example.json", message)
        self.assertNotIn("Traceback", message)
        provider_factory.assert_not_called()
        runner.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
