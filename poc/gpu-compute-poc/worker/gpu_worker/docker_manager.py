"""Docker CLI adapter for fixed, digest-pinned GPU workloads."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from .errors import DockerManagerError
from .benchmark_program import BENCHMARK_PROGRAM
from .workloads import WorkloadSpec, get_gpu_probe_image

IMAGE_PULL_TIMEOUT_SECONDS = 1800


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class DockerExecutionResult:
    image_cache_hit: bool
    payload: dict[str, object]
    container_exit_code: int
    container_id: str | None = None
    container_name: str | None = None
    image_digest: str | None = None
    workload: str | None = None


class CommandRunner(Protocol):
    async def run(
        self, args: list[str], *, timeout: float, max_output_bytes: int
    ) -> CommandResult: ...


class AsyncSubprocessRunner:
    async def run(
        self, args: list[str], *, timeout: float, max_output_bytes: int
    ) -> CommandResult:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creationflags,
            )
        except FileNotFoundError as error:
            raise DockerManagerError("DOCKER_UNAVAILABLE", "Docker CLI not found") from error
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except TimeoutError as error:
            process.kill()
            await process.wait()
            raise DockerManagerError("DOCKER_TIMEOUT", "Docker command timed out") from error
        if len(stdout_bytes) + len(stderr_bytes) > max_output_bytes:
            raise DockerManagerError("OUTPUT_LIMIT_EXCEEDED", "Docker output exceeded limit")
        return CommandResult(
            process.returncode,
            stdout_bytes.decode("utf-8", errors="replace"),
            stderr_bytes.decode("utf-8", errors="replace"),
        )


class DockerManager:
    def __init__(self, runner: CommandRunner | None = None) -> None:
        self._runner = runner or AsyncSubprocessRunner()

    async def check_environment(self) -> None:
        version = await self._run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            timeout=15,
        )
        if version.exit_code != 0:
            raise DockerManagerError("DOCKER_ENGINE_UNAVAILABLE", "Docker Engine is not running")
        info = await self._run(
            ["docker", "info", "--format", "{{.OSType}}"], timeout=15
        )
        if info.exit_code != 0 or info.stdout.strip() != "linux":
            raise DockerManagerError("DOCKER_LINUX_REQUIRED", "Docker Linux engine is required")
        probe_image = get_gpu_probe_image()
        if not await self._image_exists(probe_image):
            await self._pull(probe_image, max_output_bytes=64 * 1024)
        probe = await self._run([
            "docker", "run", "--rm", "--gpus", "all", "--network", "none",
            "--read-only", "--cap-drop", "ALL", "--security-opt",
            "no-new-privileges", probe_image, "nvidia-smi", "--query-gpu=name",
            "--format=csv,noheader",
        ], timeout=60)
        if probe.exit_code != 0 or not probe.stdout.strip():
            raise DockerManagerError(
                "DOCKER_GPU_UNAVAILABLE", "Container cannot access the NVIDIA GPU"
            )

    async def execute(
        self,
        job_id: str,
        spec: WorkloadSpec,
        *,
        worker_id: str | None = None,
        on_started: Callable[[dict[str, object]], Awaitable[None]] | None = None,
    ) -> DockerExecutionResult:
        cache_hit = await self._image_exists(spec.image)
        if not cache_hit:
            await self._pull(spec.image, max_output_bytes=spec.max_output_bytes)
            if not await self._image_exists(spec.image):
                raise DockerManagerError(
                    "IMAGE_DIGEST_MISMATCH",
                    "Pinned image is unavailable after pull",
                )

        if spec.execution_proof:
            if not worker_id or worker_id.isspace():
                raise DockerManagerError(
                    "INVALID_EXECUTION_METADATA", "worker ID is required"
                )
            return await self._execute_with_proof(
                job_id, worker_id, spec, cache_hit, on_started
            )

        safe_job = re.sub(r"[^a-zA-Z0-9_.-]", "-", job_id)[:48]
        name = f"gpu-poc-{safe_job}"
        command = [
            "docker", "run", "--name", name, "--rm", "--gpus", "all",
            "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--pids-limit", "128",
            "--memory", "4g", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            spec.image,
        ]
        try:
            result = await self._runner.run(
                command,
                timeout=spec.timeout_seconds,
                max_output_bytes=spec.max_output_bytes,
            )
        except asyncio.CancelledError:
            await self._run(["docker", "rm", "-f", name], timeout=15)
            raise
        except DockerManagerError as error:
            if error.code == "DOCKER_TIMEOUT":
                await self._run(["docker", "rm", "-f", name], timeout=15)
            raise
        if result.exit_code != 0:
            raise DockerManagerError(
                "CONTAINER_FAILED",
                _safe_error(result.stderr or result.stdout),
                exit_code=result.exit_code,
            )
        payload = _parse_gpu_result(result.stdout)
        return DockerExecutionResult(cache_hit, payload, result.exit_code)

    async def execute_benchmark(
        self,
        spec: WorkloadSpec,
        *,
        gpu_index: int,
    ) -> DockerExecutionResult:
        if spec.workload != "GPU_STANDARD_BENCHMARK":
            raise DockerManagerError(
                "BENCHMARK_FAILED", "invalid internal benchmark workload"
            )
        cache_hit = await self._image_exists(spec.image)
        if not cache_hit:
            try:
                await self._pull(spec.image, max_output_bytes=spec.max_output_bytes)
            except DockerManagerError as error:
                code = (
                    "BENCHMARK_TIMEOUT"
                    if error.code == "DOCKER_TIMEOUT"
                    else "BENCHMARK_FAILED"
                )
                raise DockerManagerError(code, str(error)) from error
            if not await self._image_exists(spec.image):
                raise DockerManagerError(
                    "BENCHMARK_FAILED", "Pinned benchmark image is unavailable after pull"
                )

        container_name = f"gpu-poc-benchmark-{gpu_index}"
        command = [
            "docker", "run", "--name", container_name, "--rm",
            "--gpus", f"device={gpu_index}", "--network", "none",
            "--read-only", "--cap-drop", "ALL", "--security-opt",
            "no-new-privileges", "--pids-limit", "128", "--memory", "4g",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "--entrypoint", "python", spec.image, "-c", BENCHMARK_PROGRAM,
        ]
        try:
            output = await self._runner.run(
                command,
                timeout=spec.timeout_seconds,
                max_output_bytes=spec.max_output_bytes,
            )
        except asyncio.CancelledError:
            await self._run(["docker", "rm", "-f", container_name], timeout=15)
            raise
        except DockerManagerError as error:
            if error.code == "DOCKER_TIMEOUT":
                await self._run(["docker", "rm", "-f", container_name], timeout=15)
                raise DockerManagerError(
                    "BENCHMARK_TIMEOUT", "GPU benchmark execution timed out"
                ) from error
            raise

        try:
            payload = _parse_benchmark_result(output.stdout)
        except DockerManagerError:
            if output.exit_code != 0:
                raise
            raise
        if output.exit_code != 0:
            raise DockerManagerError(
                "BENCHMARK_FAILED",
                _safe_error(output.stderr or output.stdout),
                exit_code=output.exit_code,
            )
        return DockerExecutionResult(cache_hit, payload, output.exit_code)

    async def _execute_with_proof(
        self,
        job_id: str,
        worker_id: str,
        spec: WorkloadSpec,
        cache_hit: bool,
        on_started: Callable[[dict[str, object]], Awaitable[None]] | None,
    ) -> DockerExecutionResult:
        safe_job = re.sub(r"[^a-zA-Z0-9_.-]", "-", job_id).strip(".-")[:48]
        if not safe_job:
            raise DockerManagerError("INVALID_EXECUTION_METADATA", "invalid job ID")
        container_name = f"gpu-poc-job-{safe_job}"
        create = [
            "docker", "create", "--name", container_name,
            "--gpus", "all", "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "128", "--memory", "4g",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "--label", f"gpu-poc.job-id={job_id}",
            "--label", f"gpu-poc.worker-id={worker_id}",
            "--label", f"gpu-poc.workload={spec.workload}",
            "--env", f"GPU_POC_JOB_ID={job_id}",
            spec.image,
        ]
        created = await self._run(create, timeout=30)
        if created.exit_code != 0:
            raise DockerManagerError(
                "CONTAINER_CREATE_FAILED", _safe_error(created.stderr or created.stdout)
            )
        container_id = created.stdout.strip()
        if not container_id or not re.fullmatch(r"[a-zA-Z0-9_.-]+", container_id):
            raise DockerManagerError(
                "INVALID_CONTAINER_ID", "Docker did not return a valid container ID"
            )

        try:
            started = await self._run(["docker", "start", container_id], timeout=30)
            if started.exit_code != 0:
                raise DockerManagerError(
                    "CONTAINER_START_FAILED", _safe_error(started.stderr or started.stdout)
                )
            proof = {
                "jobId": job_id,
                "workerId": worker_id,
                "containerId": container_id,
                "containerName": container_name,
                "workload": spec.workload,
                "imageDigest": spec.image,
            }
            if on_started is not None:
                await on_started(proof)

            waited = await self._runner.run(
                ["docker", "wait", container_id],
                timeout=spec.timeout_seconds,
                max_output_bytes=spec.max_output_bytes,
            )
            if waited.exit_code != 0:
                raise DockerManagerError(
                    "CONTAINER_WAIT_FAILED", _safe_error(waited.stderr or waited.stdout)
                )
            try:
                container_exit_code = int(waited.stdout.strip())
            except ValueError as error:
                raise DockerManagerError(
                    "INVALID_CONTAINER_RESULT", "Docker wait did not return an exit code"
                ) from error

            logs = await self._runner.run(
                ["docker", "logs", container_id],
                timeout=15,
                max_output_bytes=spec.max_output_bytes,
            )
            if logs.exit_code != 0:
                raise DockerManagerError(
                    "CONTAINER_LOGS_FAILED", _safe_error(logs.stderr or logs.stdout)
                )
            if container_exit_code != 0:
                raise DockerManagerError(
                    "CONTAINER_FAILED",
                    _safe_error(logs.stderr or logs.stdout),
                    exit_code=container_exit_code,
                )
            payload = _parse_gpu_result(logs.stdout, expected_job_id=job_id, timed=True)
            return DockerExecutionResult(
                cache_hit,
                payload,
                container_exit_code,
                container_id=container_id,
                container_name=container_name,
                image_digest=spec.image,
                workload=spec.workload,
            )
        finally:
            await self._run(["docker", "rm", "-f", container_id], timeout=15)

    async def _image_exists(self, image: str) -> bool:
        result = await self._run(
            ["docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}"],
            timeout=15,
        )
        return result.exit_code == 0

    async def _pull(self, image: str, *, max_output_bytes: int) -> None:
        result = await self._runner.run(
            ["docker", "pull", image],
            timeout=IMAGE_PULL_TIMEOUT_SECONDS,
            max_output_bytes=max_output_bytes,
        )
        if result.exit_code != 0:
            raise DockerManagerError("IMAGE_PULL_FAILED", _safe_error(result.stderr))

    async def _run(self, args: list[str], *, timeout: float) -> CommandResult:
        return await self._runner.run(args, timeout=timeout, max_output_bytes=64 * 1024)


def _parse_gpu_result(
    stdout: str,
    *,
    expected_job_id: str | None = None,
    timed: bool = False,
) -> dict[str, object]:
    markers = [line.removeprefix("GPU_TASK_RESULT=") for line in stdout.splitlines()
               if line.startswith("GPU_TASK_RESULT=")]
    if len(markers) != 1:
        raise DockerManagerError("INVALID_CONTAINER_RESULT", "Expected one GPU_TASK_RESULT")
    try:
        payload = json.loads(markers[0])
    except json.JSONDecodeError as error:
        raise DockerManagerError("INVALID_CONTAINER_RESULT", "Result is not JSON") from error
    if (
        not isinstance(payload, dict)
        or payload.get("schemaVersion") != 1
        or payload.get("status") != "SUCCESS"
        or payload.get("cudaAvailable") is not True
        or not isinstance(payload.get("gpuModel"), str)
        or not isinstance(payload.get("runtimeMs"), int)
        or payload["runtimeMs"] <= 0
        or not isinstance(payload.get("resultChecksum"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", payload["resultChecksum"])
    ):
        raise DockerManagerError("INVALID_CONTAINER_RESULT", "CUDA result validation failed")
    if expected_job_id is not None and payload.get("jobId") != expected_job_id:
        raise DockerManagerError(
            "JOB_ID_MISMATCH", "Container result job ID does not match active job"
        )
    if timed and (
        not isinstance(payload.get("iterations"), int)
        or payload["iterations"] <= 0
        or not isinstance(payload.get("matrixSize"), int)
        or payload["matrixSize"] < 2048
        or payload.get("targetDurationSeconds") != 30
        or not 28_000 <= payload["runtimeMs"] <= 35_000
    ):
        raise DockerManagerError(
            "INVALID_CONTAINER_RESULT", "Timed CUDA result validation failed"
        )
    return payload


def _parse_benchmark_result(stdout: str) -> dict[str, object]:
    markers = [
        line.removeprefix("GPU_BENCHMARK_RESULT=")
        for line in stdout.splitlines()
        if line.startswith("GPU_BENCHMARK_RESULT=")
    ]
    if len(markers) != 1:
        raise DockerManagerError(
            "BENCHMARK_FAILED", "Expected one GPU_BENCHMARK_RESULT"
        )
    try:
        payload = json.loads(markers[0])
    except json.JSONDecodeError as error:
        raise DockerManagerError("BENCHMARK_FAILED", "Benchmark result is not JSON") from error
    if not isinstance(payload, dict):
        raise DockerManagerError("BENCHMARK_FAILED", "Benchmark result must be an object")
    if payload.get("status") == "FAILED":
        code = payload.get("errorCode")
        message = payload.get("errorMessage")
        if code not in {"BENCHMARK_FAILED", "BENCHMARK_CUDA_UNAVAILABLE"}:
            code = "BENCHMARK_FAILED"
        raise DockerManagerError(
            code,
            _safe_error(message if isinstance(message, str) else "Benchmark failed"),
        )
    return payload


def _safe_error(message: str) -> str:
    compact = " ".join(message.split())
    return compact[:500] or "Docker command failed"
