import asyncio
import unittest
from unittest.mock import AsyncMock

from gpu_worker.docker_manager import DockerExecutionResult
from gpu_worker.job_manager import JobManager, WorkerRuntimeStatus
from gpu_worker.protocol import ExecuteRequest


class WorkerRuntimeStatusTests(unittest.TestCase):
    def test_a_new_worker_is_starting_but_reports_idle_to_register(self) -> None:
        status = WorkerRuntimeStatus()

        self.assertEqual(status.lifecycle(), "STARTING")
        self.assertEqual(status.get(), "IDLE")

    def test_lifecycle_follows_the_job_status_once_registered(self) -> None:
        status = WorkerRuntimeStatus()
        status.mark_registered()

        self.assertEqual(status.lifecycle(), "IDLE")
        status.set("BUSY")
        self.assertEqual(status.lifecycle(), "BUSY")

    def test_an_offline_worker_reports_offline_whatever_the_job_status(self) -> None:
        status = WorkerRuntimeStatus()
        status.mark_registered()
        status.set("BUSY")

        status.mark_offline()

        self.assertEqual(status.lifecycle(), "OFFLINE")
        self.assertEqual(status.get(), "BUSY")

    def test_register_never_sees_a_lifecycle_only_status(self) -> None:
        status = WorkerRuntimeStatus()

        for lifecycle in ("STARTING", "OFFLINE", "READY", "UNKNOWN"):
            with self.subTest(lifecycle=lifecycle), self.assertRaises(ValueError):
                status.set(lifecycle)

        status.mark_offline()
        self.assertIn(status.get(), {"IDLE", "BUSY"})


class JobManagerTest(unittest.IsolatedAsyncioTestCase):
    async def test_success_transitions_busy_to_idle(self):
        docker = AsyncMock()
        docker.execute.return_value = DockerExecutionResult(
            True,
            {"gpuModel": "NVIDIA Test GPU", "runtimeMs": 12,
             "resultChecksum": "a" * 64},
            0,
        )
        status = WorkerRuntimeStatus()
        manager = JobManager(docker, status)

        result = await manager.execute(ExecuteRequest("job-001", "GPU_PYTORCH_TEST"))

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(status.get(), "IDLE")

    async def test_duplicate_execute_while_busy_is_failed(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_execute(*args):
            started.set()
            await release.wait()
            return DockerExecutionResult(True, {
                "gpuModel": "GPU", "runtimeMs": 1,
                "resultChecksum": "a" * 64,
            }, 0)

        docker = AsyncMock()
        docker.execute.side_effect = slow_execute
        manager = JobManager(docker, WorkerRuntimeStatus())
        first = asyncio.create_task(manager.execute(
            ExecuteRequest("job-001", "GPU_PYTORCH_TEST")
        ))
        await started.wait()

        duplicate = await manager.execute(
            ExecuteRequest("job-002", "GPU_PYTORCH_TEST")
        )
        release.set()
        await first

        self.assertEqual(duplicate["status"], "FAILED")
        self.assertEqual(duplicate["errorCode"], "WORKER_BUSY")

    async def test_unknown_workload_is_rejected_without_docker(self):
        docker = AsyncMock()
        manager = JobManager(docker, WorkerRuntimeStatus())

        result = await manager.execute(ExecuteRequest("job-003", "UNKNOWN"))

        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["errorCode"], "WORKLOAD_NOT_ALLOWED")
        docker.execute.assert_not_awaited()

    async def test_30s_success_includes_execution_proof_and_started_event(self):
        docker = AsyncMock()
        execution_result = DockerExecutionResult(
            True,
            {
                "jobId": "job-30s", "gpuModel": "GPU", "runtimeMs": 30005,
                "resultChecksum": "e" * 64, "iterations": 99,
                "matrixSize": 8192, "targetDurationSeconds": 30,
            },
            0,
            container_id="cid",
            container_name="gpu-poc-job-job-30s",
            image_digest="le3560/image@sha256:" + "f" * 64,
            workload="GPU_PYTORCH_TEST_30S",
        )

        async def execute_with_started(*args, **kwargs):
            await kwargs["on_started"]({
                "jobId": "job-30s",
                "workerId": "worker-001",
                "containerId": "cid",
                "containerName": "gpu-poc-job-job-30s",
                "workload": "GPU_PYTORCH_TEST_30S",
                "imageDigest": "le3560/image@sha256:" + "f" * 64,
            })
            return execution_result

        docker.execute.side_effect = execute_with_started
        started_events = []
        manager = JobManager(docker, WorkerRuntimeStatus(), worker_id="worker-001")

        result = await manager.execute(
            ExecuteRequest("job-30s", "GPU_PYTORCH_TEST_30S"),
            progress_sink=started_events.append,
        )

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["containerId"], "cid")
        self.assertEqual(result["containerName"], "gpu-poc-job-job-30s")
        self.assertEqual(result["iterations"], 99)
        self.assertEqual(started_events[0]["type"], "EXECUTE_STARTED")
        self.assertEqual(started_events[0]["jobId"], "job-30s")
        self.assertEqual(started_events[0]["containerId"], "cid")
        kwargs = docker.execute.await_args.kwargs
        self.assertEqual(kwargs["worker_id"], "worker-001")
