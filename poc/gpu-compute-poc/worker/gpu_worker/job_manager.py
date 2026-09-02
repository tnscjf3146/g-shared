"""Single-GPU job lifecycle independent of WebSocket transport."""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Callable

from .docker_manager import DockerManager
from .errors import DockerManagerError, WorkerConfigurationError
from .protocol import ExecuteRequest
from .workloads import get_workload_spec


class WorkerRuntimeStatus:
    """Job status for the Backend, plus a wider lifecycle for local control.

    REGISTER and HEARTBEAT keep seeing exactly IDLE or BUSY. STARTING and OFFLINE
    describe this process to its own user and never reach the Backend protocol.
    """

    def __init__(self) -> None:
        self._value = "IDLE"
        self._lifecycle = "STARTING"
        self._lock = threading.Lock()

    def get(self) -> str:
        with self._lock:
            return self._value

    def set(self, value: str) -> None:
        if value not in {"IDLE", "BUSY"}:
            raise ValueError("worker status must be IDLE or BUSY")
        with self._lock:
            self._value = value

    def mark_registered(self) -> None:
        with self._lock:
            self._lifecycle = "REGISTERED"

    def mark_offline(self) -> None:
        with self._lock:
            self._lifecycle = "OFFLINE"

    def lifecycle(self) -> str:
        with self._lock:
            if self._lifecycle == "REGISTERED":
                return self._value
            return self._lifecycle


class JobManager:
    def __init__(
        self,
        docker: DockerManager,
        status: WorkerRuntimeStatus,
        *,
        worker_id: str | None = None,
    ) -> None:
        self._docker = docker
        self._status = status
        self._worker_id = worker_id
        self._lock = asyncio.Lock()

    async def execute(
        self,
        request: ExecuteRequest,
        *,
        progress_sink: Callable[[dict[str, object]], object] | None = None,
    ) -> dict[str, object]:
        if self._lock.locked():
            return _failure(request.job_id, "WORKER_BUSY", "Another job is running")
        async with self._lock:
            self._status.set("BUSY")
            try:
                spec = get_workload_spec(request.workload)

                async def on_started(proof: dict[str, object]) -> None:
                    if progress_sink is None:
                        return
                    message = {"type": "EXECUTE_STARTED", **proof}
                    pending = progress_sink(message)
                    if inspect.isawaitable(pending):
                        await pending

                if spec.execution_proof:
                    result = await self._docker.execute(
                        request.job_id,
                        spec,
                        worker_id=self._worker_id,
                        on_started=on_started,
                    )
                else:
                    result = await self._docker.execute(request.job_id, spec)
                payload = result.payload
                response = {
                    "type": "EXECUTE_RESULT",
                    "jobId": request.job_id,
                    "status": "SUCCESS",
                    "gpuModel": payload["gpuModel"],
                    "runtimeMs": payload["runtimeMs"],
                    "resultChecksum": payload["resultChecksum"],
                    "containerExitCode": result.container_exit_code,
                    "imageCacheHit": result.image_cache_hit,
                }
                for source, target in (
                    ("iterations", "iterations"),
                    ("matrixSize", "matrixSize"),
                    ("targetDurationSeconds", "targetDurationSeconds"),
                ):
                    if source in payload:
                        response[target] = payload[source]
                for value, target in (
                    (result.container_id, "containerId"),
                    (result.container_name, "containerName"),
                    (result.image_digest, "imageDigest"),
                    (result.workload, "workload"),
                ):
                    if value is not None:
                        response[target] = value
                return response
            except DockerManagerError as error:
                return _failure(
                    request.job_id,
                    error.code,
                    str(error),
                    container_exit_code=error.exit_code,
                )
            except WorkerConfigurationError as error:
                return _failure(request.job_id, "WORKLOAD_NOT_ALLOWED", str(error))
            except Exception as error:
                return _failure(request.job_id, "JOB_FAILED", str(error))
            finally:
                self._status.set("IDLE")


def _failure(
    job_id: str,
    code: str,
    message: str,
    *,
    container_exit_code: int | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "type": "EXECUTE_RESULT",
        "jobId": job_id,
        "status": "FAILED",
        "errorCode": code,
        "errorMessage": message[:500],
    }
    if container_exit_code is not None:
        payload["containerExitCode"] = container_exit_code
    return payload
