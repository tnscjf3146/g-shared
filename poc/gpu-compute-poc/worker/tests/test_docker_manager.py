import json
import unittest
from unittest.mock import AsyncMock

from gpu_worker.docker_manager import CommandResult, DockerManager
from gpu_worker.errors import DockerManagerError
from gpu_worker.workloads import get_workload_spec
from gpu_worker.workloads import GPU_STANDARD_BENCHMARK, get_system_workload_spec


class DockerManagerTest(unittest.IsolatedAsyncioTestCase):
    async def test_standard_benchmark_runs_only_fixed_hardened_command(self):
        payload = {
            "benchmarkVersion": "gpu-benchmark-v1",
            "matrixSize": 4096,
            "precision": "FP16",
            "warmupSeconds": 3,
            "measurementSeconds": 20,
            "iterations": 123,
            "elapsedMs": 20031,
            "throughputTflops": 12.34,
            "benchmarkScore": 12.34,
        }
        runner = AsyncMock()
        runner.run.side_effect = [
            CommandResult(0, "[]", ""),
            CommandResult(0, "GPU_BENCHMARK_RESULT=" + json.dumps(payload), ""),
        ]

        result = await DockerManager(runner=runner).execute_benchmark(
            get_system_workload_spec(GPU_STANDARD_BENCHMARK), gpu_index=1
        )

        self.assertTrue(result.image_cache_hit)
        command = runner.run.await_args_list[-1].args[0]
        self.assertIn(["--gpus", "device=1"], [command[index:index + 2] for index in range(len(command) - 1)])
        self.assertIn("--network", command)
        self.assertIn("none", command)
        self.assertIn("--read-only", command)
        self.assertIn("ALL", command)
        self.assertIn("no-new-privileges", command)
        self.assertIn("--entrypoint", command)
        self.assertEqual(command[-2], "-c")
        self.assertNotIn("mount", " ".join(command).lower())

    async def test_standard_benchmark_timeout_cleans_up_and_has_specific_code(self):
        runner = AsyncMock()
        runner.run.side_effect = [
            CommandResult(0, "[]", ""),
            DockerManagerError("DOCKER_TIMEOUT", "timed out"),
            CommandResult(0, "removed", ""),
        ]

        with self.assertRaises(DockerManagerError) as raised:
            await DockerManager(runner=runner).execute_benchmark(
                get_system_workload_spec(GPU_STANDARD_BENCHMARK), gpu_index=0
            )

        self.assertEqual(raised.exception.code, "BENCHMARK_TIMEOUT")
        self.assertEqual(
            runner.run.await_args_list[-1].args[0],
            ["docker", "rm", "-f", "gpu-poc-benchmark-0"],
        )

    async def test_standard_benchmark_separates_pull_and_execution_timeouts(self):
        payload = {
            "benchmarkVersion": "gpu-benchmark-v1", "matrixSize": 4096,
            "precision": "FP16", "warmupSeconds": 3,
            "measurementSeconds": 20, "iterations": 1, "elapsedMs": 20000,
            "throughputTflops": 1.0, "benchmarkScore": 1.0,
        }
        runner = AsyncMock()
        runner.run.side_effect = [
            CommandResult(1, "", "missing"),
            CommandResult(0, "pulled", ""),
            CommandResult(0, "[]", ""),
            CommandResult(0, "GPU_BENCHMARK_RESULT=" + json.dumps(payload), ""),
        ]

        await DockerManager(runner=runner).execute_benchmark(
            get_system_workload_spec(GPU_STANDARD_BENCHMARK), gpu_index=0
        )

        self.assertEqual(runner.run.await_args_list[1].kwargs["timeout"], 1800)
        self.assertEqual(runner.run.await_args_list[3].kwargs["timeout"], 90)

    async def test_cache_miss_pulls_digest_then_runs_hardened_container(self):
        payload = {
            "schemaVersion": 1,
            "status": "SUCCESS",
            "cudaAvailable": True,
            "gpuModel": "NVIDIA Test GPU",
            "runtimeMs": 12,
            "resultChecksum": "a" * 64,
            "matrixSize": 2048,
            "iterations": 8,
        }
        runner = AsyncMock()
        runner.run.side_effect = [
            CommandResult(1, "", "not found"),
            CommandResult(0, "pulled", ""),
            CommandResult(0, "[]", ""),
            CommandResult(0, "GPU_TASK_RESULT=" + json.dumps(payload), ""),
        ]
        manager = DockerManager(runner=runner)

        result = await manager.execute("job-001", get_workload_spec("GPU_PYTORCH_TEST"))

        self.assertFalse(result.image_cache_hit)
        self.assertEqual(result.payload["resultChecksum"], "a" * 64)
        commands = [call.args[0] for call in runner.run.await_args_list]
        self.assertEqual(commands[1][:2], ["docker", "pull"])
        run = commands[-1]
        self.assertIn("--gpus", run)
        self.assertIn("all", run)
        self.assertIn("--network", run)
        self.assertIn("none", run)
        self.assertNotIn("sh", run)

    async def test_cache_hit_skips_pull(self):
        payload = {
            "schemaVersion": 1, "status": "SUCCESS", "cudaAvailable": True,
            "gpuModel": "NVIDIA Test GPU", "runtimeMs": 12,
            "resultChecksum": "b" * 64, "matrixSize": 2048, "iterations": 8,
        }
        runner = AsyncMock()
        runner.run.side_effect = [
            CommandResult(0, "[]", ""),
            CommandResult(0, "GPU_TASK_RESULT=" + json.dumps(payload), ""),
        ]

        result = await DockerManager(runner=runner).execute(
            "job-002", get_workload_spec("GPU_PYTORCH_TEST")
        )

        self.assertTrue(result.image_cache_hit)
        self.assertFalse(any(
            call.args[0][1] == "pull" for call in runner.run.await_args_list
        ))

    async def test_pull_failure_has_stable_error_code(self):
        runner = AsyncMock()
        runner.run.side_effect = [
            CommandResult(1, "", "missing"),
            CommandResult(1, "", "registry unavailable"),
        ]
        with self.assertRaises(DockerManagerError) as raised:
            await DockerManager(runner=runner).execute(
                "job-003", get_workload_spec("GPU_PYTORCH_TEST")
            )
        self.assertEqual(raised.exception.code, "IMAGE_PULL_FAILED")

    async def test_container_failure_has_exit_code(self):
        runner = AsyncMock()
        runner.run.side_effect = [
            CommandResult(0, "[]", ""),
            CommandResult(20, "", "CUDA unavailable"),
        ]
        with self.assertRaises(DockerManagerError) as raised:
            await DockerManager(runner=runner).execute(
                "job-004", get_workload_spec("GPU_PYTORCH_TEST")
            )
        self.assertEqual(raised.exception.code, "CONTAINER_FAILED")
        self.assertEqual(raised.exception.exit_code, 20)

    async def test_success_exit_with_invalid_cuda_result_is_rejected(self):
        runner = AsyncMock()
        runner.run.side_effect = [
            CommandResult(0, "[]", ""),
            CommandResult(0, "GPU_TASK_RESULT={\"status\":\"SUCCESS\"}", ""),
        ]
        with self.assertRaises(DockerManagerError) as raised:
            await DockerManager(runner=runner).execute(
                "job-005", get_workload_spec("GPU_PYTORCH_TEST")
            )
        self.assertEqual(raised.exception.code, "INVALID_CONTAINER_RESULT")

    async def test_environment_rejects_stopped_engine_and_missing_container_gpu(self):
        stopped = AsyncMock()
        stopped.run.return_value = CommandResult(1, "", "engine stopped")
        with self.assertRaises(DockerManagerError) as raised:
            await DockerManager(runner=stopped).check_environment()
        self.assertEqual(raised.exception.code, "DOCKER_ENGINE_UNAVAILABLE")

        no_gpu = AsyncMock()
        no_gpu.run.side_effect = [
            CommandResult(0, "29.6.2", ""),
            CommandResult(0, "linux", ""),
            CommandResult(0, "[]", ""),
            CommandResult(1, "", "could not select device driver"),
        ]
        with self.assertRaises(DockerManagerError) as raised:
            await DockerManager(runner=no_gpu).check_environment()
        self.assertEqual(raised.exception.code, "DOCKER_GPU_UNAVAILABLE")

    async def test_30s_workload_creates_traceable_container_and_validates_job_id(self):
        payload = {
            "schemaVersion": 1, "status": "SUCCESS", "cudaAvailable": True,
            "jobId": "job-30s", "gpuModel": "NVIDIA Test GPU",
            "runtimeMs": 30010, "resultChecksum": "c" * 64,
            "matrixSize": 8192, "iterations": 200,
            "targetDurationSeconds": 30,
        }
        runner = AsyncMock()
        runner.run.side_effect = [
            CommandResult(0, "[]", ""),
            CommandResult(0, "container-id-123\n", ""),
            CommandResult(0, "", ""),
            CommandResult(0, "0\n", ""),
            CommandResult(0, "GPU_TASK_RESULT=" + json.dumps(payload), ""),
            CommandResult(0, "container-id-123\n", ""),
        ]
        started = AsyncMock()

        result = await DockerManager(runner=runner).execute(
            "job-30s",
            get_workload_spec("GPU_PYTORCH_TEST_30S"),
            worker_id="worker-001",
            on_started=started,
        )

        self.assertTrue(result.image_cache_hit)
        self.assertEqual(result.container_id, "container-id-123")
        self.assertEqual(result.container_name, "gpu-poc-job-job-30s")
        commands = [call.args[0] for call in runner.run.await_args_list]
        create = commands[1]
        self.assertEqual(create[:2], ["docker", "create"])
        self.assertIn("gpu-poc.job-id=job-30s", create)
        self.assertIn("gpu-poc.worker-id=worker-001", create)
        self.assertIn("gpu-poc.workload=GPU_PYTORCH_TEST_30S", create)
        self.assertIn("GPU_POC_JOB_ID=job-30s", create)
        self.assertEqual(commands[2], ["docker", "start", "container-id-123"])
        self.assertEqual(commands[3], ["docker", "wait", "container-id-123"])
        self.assertEqual(commands[4], ["docker", "logs", "container-id-123"])
        self.assertEqual(commands[5], ["docker", "rm", "-f", "container-id-123"])
        started.assert_awaited_once()

    async def test_30s_cache_miss_pulls_pinned_digest_before_create(self):
        payload = {
            "schemaVersion": 1, "status": "SUCCESS", "cudaAvailable": True,
            "jobId": "job-miss", "gpuModel": "NVIDIA Test GPU",
            "runtimeMs": 30000, "resultChecksum": "f" * 64,
            "matrixSize": 4096, "iterations": 10,
            "targetDurationSeconds": 30,
        }
        runner = AsyncMock()
        runner.run.side_effect = [
            CommandResult(1, "", "not found"),
            CommandResult(0, "pulled", ""),
            CommandResult(0, "[]", ""),
            CommandResult(0, "cid", ""),
            CommandResult(0, "", ""),
            CommandResult(0, "0", ""),
            CommandResult(0, "GPU_TASK_RESULT=" + json.dumps(payload), ""),
            CommandResult(0, "cid", ""),
        ]

        result = await DockerManager(runner=runner).execute(
            "job-miss",
            get_workload_spec("GPU_PYTORCH_TEST_30S"),
            worker_id="worker-001",
        )

        self.assertFalse(result.image_cache_hit)
        commands = [call.args[0] for call in runner.run.await_args_list]
        self.assertEqual(commands[1][0:2], ["docker", "pull"])
        self.assertEqual(commands[3][0:2], ["docker", "create"])

    async def test_30s_workload_rejects_container_job_id_mismatch(self):
        payload = {
            "schemaVersion": 1, "status": "SUCCESS", "cudaAvailable": True,
            "jobId": "different-job", "gpuModel": "NVIDIA Test GPU",
            "runtimeMs": 30000, "resultChecksum": "d" * 64,
            "matrixSize": 4096, "iterations": 1,
            "targetDurationSeconds": 30,
        }
        runner = AsyncMock()
        runner.run.side_effect = [
            CommandResult(0, "[]", ""),
            CommandResult(0, "cid", ""),
            CommandResult(0, "", ""),
            CommandResult(0, "0", ""),
            CommandResult(0, "GPU_TASK_RESULT=" + json.dumps(payload), ""),
            CommandResult(0, "cid", ""),
        ]

        with self.assertRaises(DockerManagerError) as raised:
            await DockerManager(runner=runner).execute(
                "job-30s",
                get_workload_spec("GPU_PYTORCH_TEST_30S"),
                worker_id="worker-001",
            )

        self.assertEqual(raised.exception.code, "JOB_ID_MISMATCH")

    async def test_30s_workload_timeout_removes_container(self):
        runner = AsyncMock()
        runner.run.side_effect = [
            CommandResult(0, "[]", ""),
            CommandResult(0, "cid", ""),
            CommandResult(0, "", ""),
            DockerManagerError("DOCKER_TIMEOUT", "Docker command timed out"),
            CommandResult(0, "cid", ""),
        ]

        with self.assertRaises(DockerManagerError) as raised:
            await DockerManager(runner=runner).execute(
                "job-timeout",
                get_workload_spec("GPU_PYTORCH_TEST_30S"),
                worker_id="worker-001",
            )

        self.assertEqual(raised.exception.code, "DOCKER_TIMEOUT")
        self.assertEqual(
            runner.run.await_args_list[-1].args[0],
            ["docker", "rm", "-f", "cid"],
        )
