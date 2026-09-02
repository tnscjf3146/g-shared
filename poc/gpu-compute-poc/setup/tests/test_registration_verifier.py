"""Tests for safe REST parsing and final Worker REGISTER verification."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import http.server
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
import urllib.error
import urllib.request

from gpu_worker.benchmark_cache import BenchmarkCache
from gpu_worker.benchmark_models import BENCHMARK_VERSION, BenchmarkResult
from gpu_worker_setup.backend import (
    BackendStatusClient,
    BackendStatusError,
    BenchmarkEvidence,
    LocalBenchmarkEvidenceSource,
    RegistrationBaseline,
    capture_registration_baseline,
    _NoRedirectHandler,
    _default_opener,
    RegistrationVerificationError,
    RegistrationVerifier,
    StatusTransportResponse,
    UrllibStatusTransport,
    WorkerStatusSnapshot,
)
from gpu_worker_setup.credentials import CredentialInput
from gpu_worker_setup.endpoint_config import BackendEndpointConfig
from gpu_worker_setup.secure_logging import SecureEventLogger


# Every timestamp below is stamped by the Backend. The Setup PC's own clock is
# deliberately offset in these tests and must never affect the outcome.
BACKEND_NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
SETUP_PC_CLOCK = datetime(2031, 4, 7, 3, 30, tzinfo=timezone.utc)


def _payload(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "workerId": "worker-001",
        "connectionStatus": "ONLINE",
        "workerStatus": "IDLE",
        "benchmark": {"version": BENCHMARK_VERSION, "score": 12.34},
        "lastHeartbeat": "2026-09-01T12:00:01Z",
        "gpuModel": "discarded",
        "workerToken": "discarded-secret",
    }
    value.update(overrides)
    return value


def _snapshot(**overrides: object) -> WorkerStatusSnapshot:
    values = {
        "worker_id": "worker-001",
        "connection_status": "ONLINE",
        "worker_status": "IDLE",
        "benchmark_version": BENCHMARK_VERSION,
        "benchmark_score": 12.34,
        "last_heartbeat": BACKEND_NOW + timedelta(seconds=1),
    }
    values.update(overrides)
    return WorkerStatusSnapshot(**values)


def _evidence(**overrides: object) -> BenchmarkEvidence:
    values = {
        "gpu_uuid": "GPU-test-uuid",
        "benchmark_version": BENCHMARK_VERSION,
        "benchmark_score": 12.34,
    }
    values.update(overrides)
    return BenchmarkEvidence(**values)


class WorkerStatusSnapshotTests(unittest.TestCase):
    def test_parser_keeps_only_exact_safe_fields(self) -> None:
        snapshot = WorkerStatusSnapshot.from_payload(_payload())

        self.assertEqual(snapshot, _snapshot())
        self.assertEqual(
            set(snapshot.__dataclass_fields__),
            {
                "worker_id",
                "connection_status",
                "worker_status",
                "benchmark_version",
                "benchmark_score",
                "last_heartbeat",
            },
        )

    def test_null_benchmark_is_a_valid_unregistered_snapshot(self) -> None:
        snapshot = WorkerStatusSnapshot.from_payload(_payload(benchmark=None))

        self.assertIsNone(snapshot.benchmark_version)
        self.assertIsNone(snapshot.benchmark_score)

    def test_parser_rejects_malformed_types_nonfinite_numbers_and_timestamps(self) -> None:
        invalid_payloads = (
            _payload(workerId=1),
            _payload(connectionStatus=True),
            _payload(connectionStatus="UNKNOWN"),
            _payload(workerStatus="STARTING"),
            _payload(benchmark=[]),
            _payload(benchmark={"version": BENCHMARK_VERSION, "score": True}),
            _payload(benchmark={"version": BENCHMARK_VERSION, "score": math.nan}),
            _payload(benchmark={"version": BENCHMARK_VERSION, "score": math.inf}),
            _payload(lastHeartbeat="not-a-timestamp"),
            _payload(lastHeartbeat="2026-09-01T12:00:01"),
        )

        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(BackendStatusError) as caught:
                WorkerStatusSnapshot.from_payload(payload)
            self.assertEqual(caught.exception.code, "BACKEND_STATUS_UNAVAILABLE")
            self.assertNotIn("discarded-secret", str(caught.exception))


class _FakeStatusTransport:
    def __init__(self, responses: list[StatusTransportResponse | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def get_json(self, url: str, headers: dict[str, str]) -> StatusTransportResponse:
        self.calls.append((url, dict(headers)))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class BackendStatusClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.credentials = CredentialInput(
            backend_url="https://api.example.com/gpu",
            worker_id="worker-001",
            worker_token="test-only-token",
        )
        self.endpoints = BackendEndpointConfig.from_base_url(
            self.credentials.backend_url
        )

    async def test_none_auth_sends_no_authorization_header(self) -> None:
        transport = _FakeStatusTransport([StatusTransportResponse(200, _payload())])

        snapshot = await BackendStatusClient(
            self.endpoints, self.credentials, transport=transport
        ).fetch("worker-001")

        self.assertEqual(snapshot, _snapshot())
        self.assertEqual(transport.calls[0][1], {"Accept": "application/json"})

    async def test_declared_bearer_sends_token_only_in_header(self) -> None:
        endpoints = replace(self.endpoints, status_authentication_mode="BEARER")
        transport = _FakeStatusTransport([StatusTransportResponse(200, _payload())])

        await BackendStatusClient(
            endpoints, self.credentials, transport=transport
        ).fetch("worker-001")

        url, headers = transport.calls[0]
        self.assertEqual(headers["Authorization"], "Bearer test-only-token")
        self.assertNotIn("test-only-token", url)
        self.assertEqual(set(headers), {"Accept", "Authorization"})

    async def test_unknown_auth_mode_fails_closed_without_transport_access(self) -> None:
        endpoints = replace(self.endpoints, status_authentication_mode="QUERY")
        transport = _FakeStatusTransport([StatusTransportResponse(200, _payload())])

        with self.assertRaises(BackendStatusError) as caught:
            await BackendStatusClient(
                endpoints, self.credentials, transport=transport
            ).fetch("worker-001")

        self.assertEqual(caught.exception.code, "BACKEND_STATUS_UNAVAILABLE")
        self.assertEqual(transport.calls, [])

    async def test_404_returns_missing_worker_for_retry(self) -> None:
        transport = _FakeStatusTransport([StatusTransportResponse(404, None)])

        snapshot = await BackendStatusClient(
            self.endpoints, self.credentials, transport=transport
        ).fetch("worker-001")

        self.assertIsNone(snapshot)

    async def test_transport_failure_exposes_only_safe_code_and_safe_log(self) -> None:
        with TemporaryDirectory() as directory:
            log_path = Path(directory) / "setup.log"
            logger = SecureEventLogger(
                log_path, timestamp_factory=lambda: "2026-09-01T12:00:00Z"
            )
            transport = _FakeStatusTransport(
                [RuntimeError("raw-response-secret Authorization: Bearer test-only-token")]
            )

            with self.assertRaises(BackendStatusError) as caught:
                await BackendStatusClient(
                    self.endpoints,
                    self.credentials,
                    transport=transport,
                    logger=logger,
                ).fetch("worker-001")

            text = log_path.read_text(encoding="utf-8")
            self.assertEqual(caught.exception.code, "BACKEND_STATUS_UNAVAILABLE")
            self.assertEqual(str(caught.exception), "BACKEND_STATUS_UNAVAILABLE")
            self.assertNotIn("raw-response-secret", text)
            self.assertNotIn("test-only-token", text)
            self.assertNotIn("Authorization", text)


class _CannedResponse:
    status = 200

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> "_CannedResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self, size: int) -> bytes:
        return self._body[:size]


class _BlockingResponse:
    status = 200

    def __enter__(self) -> "_BlockingResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self, size: int) -> bytes:
        return json.dumps(_payload()).encode("utf-8")


class StatusTransportBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_plain_http_status_url_is_refused_before_any_request(self) -> None:
        opened: list[object] = []

        def opener(request, *, timeout):
            opened.append(request)
            raise AssertionError("must not open a non-TLS status URL")

        transport = UrllibStatusTransport(opener=opener)

        with self.assertRaises(BackendStatusError):
            await transport.get_json(
                "http://api.example.com/api/workers/worker-001",
                {"Accept": "application/json"},
            )

        self.assertEqual(opened, [])

    def test_default_opener_refuses_to_follow_a_redirect(self) -> None:
        handler = _NoRedirectHandler()

        redirected = handler.redirect_request(
            urllib.request.Request("https://api.example.com/api/workers/worker-001"),
            None,
            302,
            "Found",
            {},
            "http://evil.example/x",
        )

        self.assertIsNone(redirected)

    def test_the_default_opener_actually_installs_the_refusing_handler(self) -> None:
        handlers = _default_opener().__self__.handlers

        self.assertTrue(
            any(isinstance(handler, _NoRedirectHandler) for handler in handlers)
        )
        self.assertFalse(
            any(
                type(handler) is urllib.request.HTTPRedirectHandler
                for handler in handlers
            )
        )

    async def test_a_redirect_never_forwards_the_authorization_header(self) -> None:
        received: list[dict[str, str]] = []

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                received.append({"path": self.path, **dict(self.headers)})
                if self.path.endswith("/api/workers/worker-001"):
                    self.send_response(302)
                    self.send_header("Location", "/redirected")
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args: object) -> None:
                return

        server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]

        response = await UrllibStatusTransport(allowed_schemes=("http",)).get_json(
            f"http://127.0.0.1:{port}/api/workers/worker-001",
            {"Accept": "application/json", "Authorization": "Bearer test-only-token"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            [entry["path"] for entry in received], ["/api/workers/worker-001"]
        )

    async def test_http_error_and_unusable_bodies_stay_safe_codes(self) -> None:
        def failing_opener(request, *, timeout):
            raise urllib.error.HTTPError(request.full_url, 503, "unavailable", {}, None)

        response = await UrllibStatusTransport(opener=failing_opener).get_json(
            "https://api.example.com/api/workers/worker-001", {}
        )
        self.assertEqual(response, StatusTransportResponse(503, None))

        unusable_bodies = (b"x" * 65, b"not-json", bytes([0xFF, 0xFE]), b"[]")
        for body in unusable_bodies:
            with self.subTest(body=body[:8]):
                transport = UrllibStatusTransport(
                    opener=lambda request, *, timeout, body=body: _CannedResponse(body),
                    max_body_bytes=64,
                )
                with self.assertRaises(BackendStatusError) as caught:
                    await transport.get_json(
                        "https://api.example.com/api/workers/worker-001", {}
                    )
                self.assertEqual(caught.exception.code, "BACKEND_STATUS_UNAVAILABLE")


class UrllibStatusTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocking_urllib_work_runs_off_the_event_loop(self) -> None:
        release = threading.Event()

        def blocking_opener(request, *, timeout):
            release.wait(timeout=1)
            return _BlockingResponse()

        transport = UrllibStatusTransport(opener=blocking_opener)
        timer = threading.Timer(0.25, release.set)
        timer.start()
        self.addCleanup(timer.cancel)

        task = asyncio.create_task(
            transport.get_json(
                "https://api.example.com/api/workers/worker-001",
                {"Accept": "application/json"},
            )
        )
        await asyncio.sleep(0.01)
        event_loop_remained_responsive = not task.done()
        release.set()
        response = await task

        self.assertTrue(event_loop_remained_responsive)
        self.assertEqual(response.status_code, 200)


class _FakeClock:
    def __init__(self, now: datetime = SETUP_PC_CLOCK) -> None:
        self.current = now
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += timedelta(seconds=seconds)


class _ConstantBackoff:
    def delay(self, attempt: int) -> float:
        return 1.0


class _FakeProcess:
    def __init__(self, exit_code: int | None = None) -> None:
        self.exit_code = exit_code

    def poll(self) -> int | None:
        return self.exit_code


class _SequenceStatusClient:
    def __init__(self, responses: list[WorkerStatusSnapshot | None | Exception]) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def fetch(self, worker_id: str) -> WorkerStatusSnapshot | None:
        self.calls += 1
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        return response


class RegistrationVerifierTests(unittest.IsolatedAsyncioTestCase):
    def _verifier(
        self,
        responses: list[WorkerStatusSnapshot | None | Exception],
        *,
        timeout_seconds: float = 2.0,
        baseline: RegistrationBaseline | None = None,
    ) -> tuple[RegistrationVerifier, _FakeClock, _SequenceStatusClient]:
        clock = _FakeClock()
        client = _SequenceStatusClient(responses)
        verifier = RegistrationVerifier(
            client,
            expected_worker_id="worker-001",
            baseline=baseline or RegistrationBaseline.worker_absent(),
            clock=clock,
            backoff=_ConstantBackoff(),
            timeout_seconds=timeout_seconds,
        )
        return verifier, clock, client

    async def test_acceptance_requires_every_backend_and_local_evidence_field(self) -> None:
        verifier, _, _ = self._verifier([_snapshot()])
        self.assertTrue(verifier.accepts(_snapshot(), _evidence()))

        invalid_pairs = (
            (_snapshot(worker_id="worker-002"), _evidence()),
            (_snapshot(connection_status="OFFLINE"), _evidence()),
            (_snapshot(worker_status="BUSY"), _evidence()),
            (_snapshot(benchmark_version=None, benchmark_score=None), _evidence()),
            (_snapshot(benchmark_version="old"), _evidence()),
            (_snapshot(benchmark_score=0.0), _evidence()),
            (_snapshot(benchmark_score=-1.0), _evidence(benchmark_score=-1.0)),
            (_snapshot(benchmark_score=math.inf), _evidence()),
            (_snapshot(benchmark_score=10.0), _evidence(benchmark_score=12.34)),
            (_snapshot(), _evidence(gpu_uuid="")),
            (_snapshot(), _evidence(benchmark_version="old")),
            (_snapshot(), _evidence(benchmark_score=0.0)),
            (_snapshot(), _evidence(benchmark_score=math.nan)),
        )
        for status, local in invalid_pairs:
            with self.subTest(status=status, local=local):
                self.assertFalse(verifier.accepts(status, local))

    async def test_a_setup_clock_far_ahead_of_the_backend_still_registers(self) -> None:
        verifier, clock, _ = self._verifier(
            [_snapshot()],
            baseline=RegistrationBaseline.worker_absent(),
        )
        clock.current = _snapshot().last_heartbeat + timedelta(minutes=10)

        result = await verifier.wait_until_verified(
            "worker-001", _evidence(), _FakeProcess()
        )

        self.assertEqual(result.worker_id, "worker-001")

    async def test_a_setup_clock_far_behind_the_backend_still_registers(self) -> None:
        # Only the "ahead" direction can catch a regression to the Setup PC clock;
        # a clock behind the Backend passed the old comparison too. Kept as the
        # executable statement that neither direction may matter.
        verifier, clock, _ = self._verifier(
            [_snapshot()],
            baseline=RegistrationBaseline.worker_absent(),
        )
        clock.current = _snapshot().last_heartbeat - timedelta(minutes=10)

        result = await verifier.wait_until_verified(
            "worker-001", _evidence(), _FakeProcess()
        )

        self.assertEqual(result.worker_id, "worker-001")

    async def test_a_skewed_setup_clock_still_registers_an_existing_worker(self) -> None:
        # As above, only the "ahead" offset can catch a regression to the local clock.
        previous = BACKEND_NOW - timedelta(minutes=1)
        for offset in (timedelta(minutes=10), -timedelta(minutes=10)):
            with self.subTest(offset=offset):
                verifier, clock, _ = self._verifier(
                    [_snapshot()],
                    baseline=RegistrationBaseline.from_heartbeat(previous),
                )
                clock.current = BACKEND_NOW + offset

                result = await verifier.wait_until_verified(
                    "worker-001", _evidence(), _FakeProcess()
                )

                self.assertEqual(result.last_heartbeat, _snapshot().last_heartbeat)

    async def test_a_new_worker_appearing_registers_without_any_timestamp_check(self) -> None:
        verifier, _, client = self._verifier(
            [None, _snapshot()],
            baseline=RegistrationBaseline.worker_absent(),
        )

        result = await verifier.wait_until_verified(
            "worker-001", _evidence(), _FakeProcess()
        )

        self.assertEqual(result.worker_id, "worker-001")
        self.assertEqual(result.benchmark_version, BENCHMARK_VERSION)
        self.assertEqual(client.calls, 2)

    async def test_an_existing_heartbeat_that_advances_registers(self) -> None:
        previous = BACKEND_NOW - timedelta(seconds=30)
        verifier, _, _ = self._verifier(
            [_snapshot(last_heartbeat=previous), _snapshot()],
            baseline=RegistrationBaseline.from_heartbeat(previous),
        )

        result = await verifier.wait_until_verified(
            "worker-001", _evidence(), _FakeProcess()
        )

        self.assertEqual(result.last_heartbeat, _snapshot().last_heartbeat)

    async def test_an_existing_record_repeated_unchanged_never_registers(self) -> None:
        previous = BACKEND_NOW - timedelta(seconds=30)
        verifier, _, _ = self._verifier(
            [_snapshot(last_heartbeat=previous)],
            baseline=RegistrationBaseline.from_heartbeat(previous),
        )

        with self.assertRaises(RegistrationVerificationError) as caught:
            await verifier.wait_until_verified(
                "worker-001", _evidence(), _FakeProcess()
            )

        self.assertEqual(caught.exception.code, "REGISTER_TIMEOUT")

    async def test_an_unknown_baseline_requires_an_observed_advance(self) -> None:
        verifier, _, _ = self._verifier(
            [_snapshot()],
            baseline=RegistrationBaseline.heartbeat_unknown(),
        )

        with self.assertRaises(RegistrationVerificationError) as caught:
            await verifier.wait_until_verified(
                "worker-001", _evidence(), _FakeProcess()
            )

        self.assertEqual(caught.exception.code, "REGISTER_TIMEOUT")

    async def test_an_unknown_baseline_registers_once_the_heartbeat_advances(self) -> None:
        verifier, _, _ = self._verifier(
            [_snapshot(last_heartbeat=BACKEND_NOW), _snapshot()],
            baseline=RegistrationBaseline.heartbeat_unknown(),
        )

        result = await verifier.wait_until_verified(
            "worker-001", _evidence(), _FakeProcess()
        )

        self.assertEqual(result.last_heartbeat, _snapshot().last_heartbeat)

    async def test_the_public_predicate_enforces_its_own_baseline(self) -> None:
        unchanged = _snapshot()
        verifier, _, _ = self._verifier(
            [unchanged],
            baseline=RegistrationBaseline.from_heartbeat(unchanged.last_heartbeat),
        )

        self.assertFalse(verifier.accepts(unchanged, _evidence()))
        self.assertTrue(
            verifier.accepts(
                _snapshot(last_heartbeat=unchanged.last_heartbeat + timedelta(seconds=1)),
                _evidence(),
            )
        )

    async def test_the_public_predicate_cannot_decide_an_unknown_baseline(self) -> None:
        verifier, _, _ = self._verifier(
            [_snapshot()], baseline=RegistrationBaseline.heartbeat_unknown()
        )

        self.assertFalse(verifier.accepts(_snapshot(), _evidence()))

    async def test_a_foreign_heartbeat_is_never_adopted_as_the_baseline(self) -> None:
        verifier, _, _ = self._verifier(
            [
                _snapshot(
                    worker_id="worker-002",
                    last_heartbeat=BACKEND_NOW + timedelta(days=365),
                ),
                _snapshot(last_heartbeat=BACKEND_NOW),
                _snapshot(),
            ],
            timeout_seconds=3.0,
            baseline=RegistrationBaseline.heartbeat_unknown(),
        )

        result = await verifier.wait_until_verified(
            "worker-001", _evidence(), _FakeProcess()
        )

        self.assertEqual(result.last_heartbeat, _snapshot().last_heartbeat)

    async def test_wait_returns_registration_evidence_after_current_status(self) -> None:
        verifier, clock, _ = self._verifier([None, _snapshot()])

        result = await verifier.wait_until_verified(
            "worker-001", _evidence(), _FakeProcess()
        )

        self.assertEqual(result.worker_id, "worker-001")
        self.assertEqual(result.gpu_uuid, "GPU-test-uuid")
        self.assertEqual(result.last_heartbeat, BACKEND_NOW + timedelta(seconds=1))
        self.assertEqual(clock.sleeps, [1.0])

    async def test_missing_worker_until_deadline_is_register_timeout(self) -> None:
        verifier, _, _ = self._verifier([None])

        with self.assertRaises(RegistrationVerificationError) as caught:
            await verifier.wait_until_verified(
                "worker-001", _evidence(), _FakeProcess()
            )

        self.assertEqual(caught.exception.code, "REGISTER_TIMEOUT")

    async def test_process_exit_is_distinct_and_skips_backend_access(self) -> None:
        verifier, _, client = self._verifier([_snapshot()])

        with self.assertRaises(RegistrationVerificationError) as caught:
            await verifier.wait_until_verified(
                "worker-001", _evidence(), _FakeProcess(exit_code=4)
            )

        self.assertEqual(caught.exception.code, "WORKER_PROCESS_EXITED")
        self.assertEqual(client.calls, 0)

    async def test_backend_failures_until_deadline_are_status_unavailable(self) -> None:
        verifier, _, _ = self._verifier(
            [BackendStatusError("BACKEND_STATUS_UNAVAILABLE")]
        )

        with self.assertRaises(RegistrationVerificationError) as caught:
            await verifier.wait_until_verified(
                "worker-001", _evidence(), _FakeProcess()
            )

        self.assertEqual(caught.exception.code, "BACKEND_STATUS_UNAVAILABLE")

    async def test_a_slow_poll_cannot_outlive_the_registration_deadline(self) -> None:
        class _SlowStatusClient:
            async def fetch(self, worker_id: str) -> WorkerStatusSnapshot | None:
                await asyncio.sleep(30)
                raise AssertionError("the poll must be cut short by the deadline")

        verifier = RegistrationVerifier(
            _SlowStatusClient(),
            expected_worker_id="worker-001",
            baseline=RegistrationBaseline.worker_absent(),
            backoff=_ConstantBackoff(),
            timeout_seconds=0.2,
        )

        started = time.monotonic()
        with self.assertRaises(RegistrationVerificationError) as caught:
            await verifier.wait_until_verified(
                "worker-001", _evidence(), _FakeProcess()
            )

        self.assertLess(time.monotonic() - started, 10.0)
        self.assertEqual(caught.exception.code, "BACKEND_STATUS_UNAVAILABLE")

    async def test_one_transient_blip_does_not_rename_a_later_timeout(self) -> None:
        verifier, _, _ = self._verifier(
            [BackendStatusError(), _snapshot(worker_status="BUSY")]
        )

        with self.assertRaises(RegistrationVerificationError) as caught:
            await verifier.wait_until_verified(
                "worker-001", _evidence(), _FakeProcess()
            )

        self.assertEqual(caught.exception.code, "REGISTER_TIMEOUT")

    async def test_an_expired_deadline_times_out_without_polling_the_backend(self) -> None:
        verifier, _, client = self._verifier([_snapshot()], timeout_seconds=0.0)

        with self.assertRaises(RegistrationVerificationError) as caught:
            await verifier.wait_until_verified(
                "worker-001", _evidence(), _FakeProcess()
            )

        self.assertEqual(caught.exception.code, "REGISTER_TIMEOUT")
        self.assertEqual(client.calls, 0)

    async def test_an_earlier_benchmark_failure_does_not_rename_a_later_timeout(self) -> None:
        verifier, _, _ = self._verifier(
            [_snapshot(), _snapshot(worker_status="BUSY")]
        )

        with self.assertRaises(RegistrationVerificationError) as caught:
            await verifier.wait_until_verified(
                "worker-001", _evidence(benchmark_version="old"), _FakeProcess()
            )

        self.assertEqual(caught.exception.code, "REGISTER_TIMEOUT")

    async def test_absent_local_evidence_fails_with_a_stable_benchmark_code(self) -> None:
        verifier, _, client = self._verifier([_snapshot()])

        with self.assertRaises(RegistrationVerificationError) as caught:
            await verifier.wait_until_verified("worker-001", None, _FakeProcess())

        self.assertEqual(caught.exception.code, "BENCHMARK_REGISTRATION_FAILED")
        self.assertEqual(client.calls, 0)

    async def test_online_busy_never_completes_and_stays_register_timeout(self) -> None:
        verifier, _, _ = self._verifier([_snapshot(worker_status="BUSY")])

        with self.assertRaises(RegistrationVerificationError) as caught:
            await verifier.wait_until_verified(
                "worker-001", _evidence(), _FakeProcess()
            )

        self.assertEqual(caught.exception.code, "REGISTER_TIMEOUT")

    async def test_stale_heartbeat_is_a_register_timeout_not_a_benchmark_failure(self) -> None:
        verifier, _, _ = self._verifier(
            [_snapshot()],
            baseline=RegistrationBaseline.from_heartbeat(
                _snapshot().last_heartbeat
            ),
        )

        with self.assertRaises(RegistrationVerificationError) as caught:
            await verifier.wait_until_verified(
                "worker-001", _evidence(), _FakeProcess()
            )

        self.assertEqual(caught.exception.code, "REGISTER_TIMEOUT")

    async def test_local_cache_mismatch_is_reported_as_benchmark_failure(self) -> None:
        verifier, _, _ = self._verifier([_snapshot()])

        with self.assertRaises(RegistrationVerificationError) as caught:
            await verifier.wait_until_verified(
                "worker-001", _evidence(benchmark_version="old"), _FakeProcess()
            )

        self.assertEqual(caught.exception.code, "BENCHMARK_REGISTRATION_FAILED")

    async def test_declared_mismatch_exit_code_is_reported_as_credential_mismatch(self) -> None:
        clock = _FakeClock()
        verifier = RegistrationVerifier(
            _SequenceStatusClient([_snapshot()]),
            expected_worker_id="worker-001",
            baseline=RegistrationBaseline.worker_absent(),
            clock=clock,
            backoff=_ConstantBackoff(),
            timeout_seconds=2.0,
            mismatch_exit_codes=(4,),
        )

        with self.assertRaises(RegistrationVerificationError) as caught:
            await verifier.wait_until_verified(
                "worker-001", _evidence(), _FakeProcess(exit_code=4)
            )

        self.assertEqual(caught.exception.code, "WORKER_CREDENTIAL_MISMATCH")

    async def test_undeclared_exit_code_remains_a_generic_process_exit(self) -> None:
        clock = _FakeClock()
        verifier = RegistrationVerifier(
            _SequenceStatusClient([_snapshot()]),
            expected_worker_id="worker-001",
            baseline=RegistrationBaseline.worker_absent(),
            clock=clock,
            backoff=_ConstantBackoff(),
            timeout_seconds=2.0,
            mismatch_exit_codes=(4,),
        )

        with self.assertRaises(RegistrationVerificationError) as caught:
            await verifier.wait_until_verified(
                "worker-001", _evidence(), _FakeProcess(exit_code=3)
            )

        self.assertEqual(caught.exception.code, "WORKER_PROCESS_EXITED")

    async def test_online_idle_without_current_benchmark_is_benchmark_failure(self) -> None:
        verifier, _, _ = self._verifier(
            [_snapshot(benchmark_version=None, benchmark_score=None)]
        )

        with self.assertRaises(RegistrationVerificationError) as caught:
            await verifier.wait_until_verified(
                "worker-001", _evidence(), _FakeProcess()
            )

        self.assertEqual(caught.exception.code, "BENCHMARK_REGISTRATION_FAILED")


def _cached_result(gpu_uuid: str = "GPU-test-uuid") -> BenchmarkResult:
    return BenchmarkResult(
        benchmark_version=BENCHMARK_VERSION,
        gpu_uuid=gpu_uuid,
        gpu_model="Test GPU",
        vram_mb=12288,
        matrix_size=4096,
        precision="FP16",
        warmup_seconds=3,
        measurement_seconds=20,
        iterations=100,
        elapsed_ms=20000,
        throughput_tflops=12.34,
        benchmark_score=12.34,
        measured_at="2026-09-01T11:59:00Z",
    )


class LocalBenchmarkEvidenceSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = TemporaryDirectory()
        self.addCleanup(self._temporary_directory.cleanup)
        self.directory = Path(self._temporary_directory.name)
        self.cache = BenchmarkCache(self.directory, logger=lambda message: None)

    def test_valid_cache_for_the_current_gpu_becomes_local_evidence(self) -> None:
        self.cache.save(_cached_result())

        evidence = LocalBenchmarkEvidenceSource(self.cache).evidence_for(
            "GPU-test-uuid"
        )

        self.assertEqual(evidence, _evidence())

    def test_absent_cache_yields_no_local_evidence(self) -> None:
        evidence = LocalBenchmarkEvidenceSource(self.cache).evidence_for(
            "GPU-test-uuid"
        )

        self.assertIsNone(evidence)

    def test_cache_for_a_different_gpu_uuid_yields_no_local_evidence(self) -> None:
        self.cache.save(_cached_result(gpu_uuid="GPU-other-uuid"))

        evidence = LocalBenchmarkEvidenceSource(self.cache).evidence_for(
            "GPU-test-uuid"
        )

        self.assertIsNone(evidence)


class RegistrationBaselineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.credentials = CredentialInput(
            backend_url="https://api.example.com/gpu",
            worker_id="worker-001",
            worker_token="test-only-token",
        )
        self.endpoints = BackendEndpointConfig.from_base_url(
            self.credentials.backend_url
        )

    def _client(self, responses: list[object]) -> BackendStatusClient:
        return BackendStatusClient(
            self.endpoints,
            self.credentials,
            transport=_FakeStatusTransport(responses),
        )

    async def test_an_absent_worker_becomes_a_new_registration_baseline(self) -> None:
        baseline = await capture_registration_baseline(
            self._client([StatusTransportResponse(404, None)]), "worker-001"
        )

        self.assertEqual(baseline, RegistrationBaseline.worker_absent())
        self.assertIsNone(baseline.last_heartbeat)

    async def test_an_existing_worker_keeps_the_backend_stamped_heartbeat(self) -> None:
        baseline = await capture_registration_baseline(
            self._client([StatusTransportResponse(200, _payload())]), "worker-001"
        )

        self.assertEqual(
            baseline,
            RegistrationBaseline.from_heartbeat(BACKEND_NOW + timedelta(seconds=1)),
        )

    async def test_a_record_for_a_different_worker_yields_an_unknown_baseline(self) -> None:
        baseline = await capture_registration_baseline(
            self._client(
                [StatusTransportResponse(200, _payload(workerId="worker-002"))]
            ),
            "worker-001",
        )

        self.assertEqual(baseline, RegistrationBaseline.heartbeat_unknown())
        self.assertIsNone(baseline.last_heartbeat)

    async def test_an_unreadable_backend_yields_an_unknown_baseline(self) -> None:
        baseline = await capture_registration_baseline(
            self._client([RuntimeError("backend down")]), "worker-001"
        )

        self.assertEqual(baseline, RegistrationBaseline.heartbeat_unknown())
        self.assertIsNone(baseline.last_heartbeat)

    def test_a_naive_baseline_heartbeat_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RegistrationBaseline.from_heartbeat(datetime(2026, 9, 1, 12, 0))

    def test_an_absent_worker_may_not_carry_a_baseline_heartbeat(self) -> None:
        with self.assertRaises(ValueError):
            RegistrationBaseline(worker_existed=False, last_heartbeat=BACKEND_NOW)

    def test_a_non_boolean_discriminant_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RegistrationBaseline(worker_existed="nope")

    def test_a_non_datetime_baseline_heartbeat_is_rejected(self) -> None:
        for value in ("2026-09-01T12:00:00Z", 1_756_728_000):
            with self.subTest(value=value), self.assertRaises(ValueError):
                RegistrationBaseline.from_heartbeat(value)

    def test_a_baseline_heartbeat_is_normalised_to_utc(self) -> None:
        offset = timezone(timedelta(hours=9))

        baseline = RegistrationBaseline.from_heartbeat(
            BACKEND_NOW.astimezone(offset)
        )

        self.assertEqual(baseline.last_heartbeat, BACKEND_NOW)
        self.assertEqual(baseline.last_heartbeat.utcoffset(), timedelta(0))

    def test_the_verifier_requires_a_real_baseline(self) -> None:
        with self.assertRaises(ValueError):
            RegistrationVerifier(
                _SequenceStatusClient([]),
                expected_worker_id="worker-001",
                baseline=BACKEND_NOW,
            )


if __name__ == "__main__":
    unittest.main()
