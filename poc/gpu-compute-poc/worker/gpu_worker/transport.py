"""Single-connection raw WebSocket transport for the STEP 2 Worker."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

from websockets.asyncio.client import connect

from .errors import WorkerConfigurationError
from .job_manager import JobManager
from .protocol import decode_execute


@dataclass(frozen=True)
class WorkerClientConfig:
    backend_url: str
    worker_id: str
    hourly_price: int
    gpu_index: int
    supported_workloads: tuple[str, ...]
    heartbeat_interval_seconds: float = 5.0
    worker_token: str | None = None

    def __post_init__(self) -> None:
        parsed = urlparse(self.backend_url)
        if parsed.scheme not in {"ws", "wss"} or not parsed.netloc:
            raise WorkerConfigurationError("backend_url must be an absolute ws:// or wss:// URL")
        if not self.worker_id or self.worker_id.isspace():
            raise WorkerConfigurationError("worker_id must not be blank")
        if (
            isinstance(self.hourly_price, bool)
            or not isinstance(self.hourly_price, int)
            or self.hourly_price < 0
        ):
            raise WorkerConfigurationError("hourly_price must be a non-negative integer")
        if (
            isinstance(self.gpu_index, bool)
            or not isinstance(self.gpu_index, int)
            or self.gpu_index < 0
        ):
            raise WorkerConfigurationError("gpu_index must be a non-negative integer")
        if not self.supported_workloads or any(
            not workload or workload.isspace()
            for workload in self.supported_workloads
        ):
            raise WorkerConfigurationError(
                "supported_workloads must contain only non-blank values"
            )
        if self.heartbeat_interval_seconds <= 0:
            raise WorkerConfigurationError(
                "heartbeat_interval_seconds must be greater than zero"
            )


class WorkerMessageSource(Protocol):
    def register_payload(self) -> dict[str, object]: ...

    def heartbeat_payload(self) -> dict[str, object]: ...


async def run_connection(
    config: WorkerClientConfig,
    source: WorkerMessageSource,
    *,
    connector: Callable = connect,
    wait: Callable[[float], Awaitable[None]] = asyncio.sleep,
    job_manager: JobManager | None = None,
) -> None:
    """Maintain one Backend connection until it closes; never reconnect."""
    connection = connector(config.backend_url, additional_headers={
        "Authorization": f"Bearer {config.worker_token}"
    }) if config.worker_token else connector(config.backend_url)
    async with connection as websocket:
        await websocket.send(_encode(source.register_payload()))
        if job_manager is None:
            while True:
                await wait(config.heartbeat_interval_seconds)
                await websocket.send(_encode(source.heartbeat_payload()))

        outbound: asyncio.Queue[dict[str, object]] = asyncio.Queue()

        async def writer() -> None:
            while True:
                await websocket.send(_encode(await outbound.get()))

        async def heartbeat() -> None:
            while True:
                await wait(config.heartbeat_interval_seconds)
                await outbound.put(source.heartbeat_payload())

        jobs: set[asyncio.Task[None]] = set()

        async def execute(request) -> None:
            await outbound.put(await job_manager.execute(
                request,
                progress_sink=outbound.put,
            ))

        async def receiver() -> None:
            async for raw in websocket:
                request = decode_execute(raw)
                task = asyncio.create_task(execute(request))
                jobs.add(task)
                task.add_done_callback(jobs.discard)

        tasks = {
            asyncio.create_task(writer()),
            asyncio.create_task(heartbeat()),
            asyncio.create_task(receiver()),
        }
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks | jobs:
                task.cancel()
            await asyncio.gather(*(tasks | jobs), return_exceptions=True)


def _encode(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
