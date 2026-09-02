"""Configuration and entry point for the STEP 2 Worker WebSocket client."""

from __future__ import annotations

import argparse
import asyncio
import sys
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from websockets.exceptions import WebSocketException

from .agent_config import resolve_settings
from .benchmark_cache import BenchmarkCache
from .benchmark_runner import BenchmarkRunner
from .docker_manager import DockerManager
from .environment import EnvironmentManager, SetupStatus
from .errors import SetupRequiredError, WorkerAlreadyRunningError, WorkerError
from .exit_codes import WorkerExitCode
from .instance_guard import WorkerInstanceGuard
from .job_manager import JobManager, WorkerRuntimeStatus
from .messages import NvmlWorkerMessageSource, WorkerMetadata
from .runtime import NvmlGpuStatusProvider
from .transport import WorkerClientConfig, run_connection


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Connect this CUDA Worker to the STEP 2 Backend. Without arguments the "
            "agent reads worker-config.json from its own directory."
        )
    )
    parser.add_argument("--backend-url", default=None)
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--hourly-price", type=int, default=None)
    parser.add_argument("--gpu-index", type=int, default=None)
    parser.add_argument(
        "--supported-workload",
        action="append",
        dest="supported_workloads",
        default=None,
    )
    return parser


def config_from_args(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    directory: Path | None = None,
) -> WorkerClientConfig:
    args = build_parser().parse_args(argv)
    settings = resolve_settings(
        args,
        env=os.environ if env is None else env,
        directory=directory,
    )
    if settings.config_path is not None:
        print(f"Config: {settings.config_path}")
    return WorkerClientConfig(
        backend_url=settings.backend_url,
        worker_id=settings.worker_id,
        hourly_price=settings.hourly_price,
        gpu_index=settings.gpu_index,
        supported_workloads=settings.supported_workloads,
        worker_token=settings.worker_token,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    directory: Path | None = None,
    provider_factory: Any = NvmlGpuStatusProvider,
    connection_runner: Any = run_connection,
    docker_factory: Any = DockerManager,
    benchmark_cache_factory: Any = BenchmarkCache,
    benchmark_runner_factory: Any = BenchmarkRunner,
    guard_factory: Any = WorkerInstanceGuard,
) -> int:
    try:
        config = config_from_args(argv, env=env, directory=directory)
        guard = guard_factory(config.worker_id, config.gpu_index)
        if not guard.acquire():
            # Another Worker already serves this GPU for this user. Stop before any
            # NVML, Docker, or Backend work so nothing races the running instance.
            print(
                f"Worker client stopped: {WorkerExitCode.ALREADY_RUNNING.issue_code}",
                file=sys.stderr,
            )
            return WorkerExitCode.ALREADY_RUNNING
        metadata = WorkerMetadata(
            worker_id=config.worker_id,
            hourly_price=config.hourly_price,
            supported_workloads=config.supported_workloads,
        )
        try:
            return _run_registered_worker(
                config,
                metadata,
                directory=directory,
                provider_factory=provider_factory,
                connection_runner=connection_runner,
                docker_factory=docker_factory,
                benchmark_cache_factory=benchmark_cache_factory,
                benchmark_runner_factory=benchmark_runner_factory,
            )
        finally:
            # A failure to hand back the identity must not rewrite a clean run's
            # result; the operating system releases it when this process ends.
            try:
                guard.release()
            except OSError:
                pass
    except KeyboardInterrupt:
        print("Worker client stopped", file=sys.stderr)
        return WorkerExitCode.OK
    except WorkerAlreadyRunningError:
        # Not an installation failure: another Worker already owns this identity,
        # and Setup verifies that process's Backend state instead.
        print(
            f"Worker client stopped: {WorkerExitCode.ALREADY_RUNNING.issue_code}",
            file=sys.stderr,
        )
        return WorkerExitCode.ALREADY_RUNNING
    except WorkerError as exc:
        print(f"Worker client failed: {exc}", file=sys.stderr)
        return WorkerExitCode.EXPECTED_FAILURE
    except (OSError, WebSocketException) as exc:
        print(f"Worker WebSocket failed: {exc}", file=sys.stderr)
        return WorkerExitCode.TRANSPORT_FAILURE


def _run_registered_worker(
    config: WorkerClientConfig,
    metadata: WorkerMetadata,
    *,
    directory: Path | None,
    provider_factory: Any,
    connection_runner: Any,
    docker_factory: Any,
    benchmark_cache_factory: Any,
    benchmark_runner_factory: Any,
) -> int:
    with provider_factory(config.gpu_index) as provider:
        docker = docker_factory()
        runtime_status = WorkerRuntimeStatus()
        job_manager = JobManager(
            docker,
            runtime_status,
            worker_id=config.worker_id,
        )

        async def run_ready_agent() -> None:
            report = await EnvironmentManager(provider, docker).inspect()
            if report.status != SetupStatus.READY:
                raise SetupRequiredError(
                    f"SETUP_REQUIRED [{report.error_code}]: {report.message}"
                )
            print("Environment: READY")
            benchmark_cache = benchmark_cache_factory(directory=directory)
            benchmark_runner = benchmark_runner_factory(
                docker,
                benchmark_cache,
                gpu_index=config.gpu_index,
            )
            benchmark = await benchmark_runner.ensure(provider.snapshot())
            source = NvmlWorkerMessageSource(
                provider,
                metadata,
                status_provider=runtime_status.get,
                benchmark=benchmark,
            )
            runtime_status.mark_registered()
            try:
                await connection_runner(
                    config,
                    source,
                    job_manager=job_manager,
                )
            finally:
                runtime_status.mark_offline()

        asyncio.run(run_ready_agent())
    return WorkerExitCode.OK


if __name__ == "__main__":
    raise SystemExit(main())
