"""Tests for the one-connection Worker WebSocket transport."""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock

from gpu_worker.transport import WorkerClientConfig, run_connection


class FakeSocket:
    def __init__(self, *, fail_on_send: int | None = None) -> None:
        self.sent: list[str] = []
        self._fail_on_send = fail_on_send
        self.incoming: asyncio.Queue[str] = asyncio.Queue()

    async def send(self, payload: str) -> None:
        if self._fail_on_send == len(self.sent) + 1:
            raise ConnectionError("server closed")
        self.sent.append(payload)

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        return await self.incoming.get()


class FakeConnector:
    def __init__(self, socket: FakeSocket) -> None:
        self.socket = socket
        self.calls = 0

    def __call__(self, url: str, **kwargs) -> "FakeConnector":
        self.calls += 1
        self.url = url
        self.kwargs = kwargs
        return self

    async def __aenter__(self) -> FakeSocket:
        return self.socket

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class FakeMessageSource:
    def register_payload(self) -> dict[str, object]:
        return {"type": "REGISTER", "workerId": "worker-001"}

    def heartbeat_payload(self) -> dict[str, object]:
        return {"type": "HEARTBEAT", "workerId": "worker-001"}


def config() -> WorkerClientConfig:
    return WorkerClientConfig(
        backend_url="ws://127.0.0.1:8080/ws/workers",
        worker_id="worker-001",
        hourly_price=600,
        gpu_index=0,
        supported_workloads=("EMBEDDING",),
    )


class WorkerTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_register_precedes_five_second_heartbeat(self) -> None:
        socket = FakeSocket()
        connector = FakeConnector(socket)
        wait = AsyncMock(side_effect=[None, asyncio.CancelledError()])

        with self.assertRaises(asyncio.CancelledError):
            await run_connection(
                config(),
                FakeMessageSource(),
                connector=connector,
                wait=wait,
            )

        self.assertEqual(json.loads(socket.sent[0])["type"], "REGISTER")
        self.assertEqual(json.loads(socket.sent[1])["type"], "HEARTBEAT")
        self.assertNotIn(" ", socket.sent[0])
        wait.assert_any_await(5.0)
        self.assertEqual(connector.calls, 1)

    async def test_server_close_propagates_without_reconnect(self) -> None:
        socket = FakeSocket(fail_on_send=2)
        connector = FakeConnector(socket)
        wait = AsyncMock(return_value=None)

        with self.assertRaisesRegex(ConnectionError, "server closed"):
            await run_connection(
                config(),
                FakeMessageSource(),
                connector=connector,
                wait=wait,
            )

        self.assertEqual(connector.calls, 1)
        self.assertEqual(len(socket.sent), 1)

    async def test_worker_token_is_sent_only_as_authorization_header(self) -> None:
        socket = FakeSocket()
        connector = FakeConnector(socket)
        wait = AsyncMock(side_effect=asyncio.CancelledError())
        secured = WorkerClientConfig(
            backend_url="wss://backend.example/ws/workers",
            worker_id="worker-001", hourly_price=600, gpu_index=0,
            supported_workloads=("GPU_PYTORCH_TEST",),
            worker_token="secret-token",
        )

        with self.assertRaises(asyncio.CancelledError):
            await run_connection(
                secured, FakeMessageSource(), connector=connector, wait=wait
            )

        self.assertEqual(
            connector.kwargs["additional_headers"]["Authorization"],
            "Bearer secret-token",
        )
        self.assertNotIn("secret-token", connector.url)
        self.assertNotIn("secret-token", socket.sent[0])

    async def test_heartbeat_continues_while_execute_is_running(self) -> None:
        socket = FakeSocket()
        connector = FakeConnector(socket)
        heartbeat_due = asyncio.Event()
        never = asyncio.Event()
        wait_calls = 0

        async def controlled_wait(seconds: float) -> None:
            nonlocal wait_calls
            wait_calls += 1
            if wait_calls == 1:
                await heartbeat_due.wait()
            else:
                await never.wait()

        started = asyncio.Event()
        release = asyncio.Event()

        class BlockingJobManager:
            async def execute(self, request, *, progress_sink=None):
                started.set()
                await release.wait()
                return {
                    "type": "EXECUTE_RESULT", "jobId": request.job_id,
                    "status": "SUCCESS", "gpuModel": "GPU", "runtimeMs": 1,
                    "resultChecksum": "a" * 64, "containerExitCode": 0,
                    "imageCacheHit": True,
                }

        connection = asyncio.create_task(run_connection(
            config(), FakeMessageSource(), connector=connector,
            wait=controlled_wait, job_manager=BlockingJobManager(),
        ))
        await asyncio.sleep(0)
        await socket.incoming.put(json.dumps({
            "type": "EXECUTE",
            "jobId": "f271e9f5-0d9f-4fbe-bac1-8812647a02ba",
            "workload": "GPU_PYTORCH_TEST",
        }))
        await started.wait()
        heartbeat_due.set()
        for _ in range(10):
            await asyncio.sleep(0)
            if any(json.loads(item)["type"] == "HEARTBEAT" for item in socket.sent):
                break

        self.assertTrue(any(
            json.loads(item)["type"] == "HEARTBEAT" for item in socket.sent
        ))
        self.assertFalse(release.is_set())

        release.set()
        for _ in range(10):
            await asyncio.sleep(0)
            if any(json.loads(item)["type"] == "EXECUTE_RESULT" for item in socket.sent):
                break
        self.assertTrue(any(
            json.loads(item)["type"] == "EXECUTE_RESULT" for item in socket.sent
        ))
        connection.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await connection

    async def test_result_send_failure_ends_connection_without_reconnect(self) -> None:
        socket = FakeSocket(fail_on_send=2)
        connector = FakeConnector(socket)
        never = asyncio.Event()

        async def blocked_wait(seconds: float) -> None:
            await never.wait()

        class ImmediateJobManager:
            async def execute(self, request, *, progress_sink=None):
                return {
                    "type": "EXECUTE_RESULT", "jobId": request.job_id,
                    "status": "FAILED", "errorCode": "CONTAINER_FAILED",
                    "errorMessage": "test failure",
                }

        connection = asyncio.create_task(run_connection(
            config(), FakeMessageSource(), connector=connector,
            wait=blocked_wait, job_manager=ImmediateJobManager(),
        ))
        await asyncio.sleep(0)
        await socket.incoming.put(json.dumps({
            "type": "EXECUTE",
            "jobId": "f271e9f5-0d9f-4fbe-bac1-8812647a02ba",
            "workload": "GPU_PYTORCH_TEST",
        }))

        with self.assertRaisesRegex(ConnectionError, "server closed"):
            await asyncio.wait_for(connection, timeout=1)
        self.assertEqual(connector.calls, 1)


if __name__ == "__main__":
    unittest.main()
