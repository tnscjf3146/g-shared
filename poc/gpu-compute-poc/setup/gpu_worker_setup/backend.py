"""Explicit, secret-safe Backend clients and Worker registration verification."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import math
import re
import ssl
from typing import Protocol
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from gpu_worker.benchmark_models import BENCHMARK_VERSION, BenchmarkResult

from .credentials import CredentialInput
from .endpoint_config import BackendEndpointConfig
from .secure_logging import SecureEventLogger


_WORKER_ID = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,62}[A-Za-z0-9])?$")
_CONNECTION_STATUSES = frozenset({"ONLINE", "OFFLINE"})
_WORKER_STATUSES = frozenset({"IDLE", "BUSY"})
_MAX_STATUS_BODY_BYTES = 1_048_576
# This Backend's handshake interceptor refuses a Worker token by returning false
# without setting a status, so Spring completes the request as 200 with no upgrade.
_TOKEN_REJECTED_STATUSES = frozenset({200, 401, 403})
_ENDPOINT_ABSENT_STATUSES = frozenset({404, 410})


def is_valid_worker_id(value: object) -> bool:
    """Apply the bounded identifier policy before any Backend access."""
    return isinstance(value, str) and bool(_WORKER_ID.fullmatch(value))


class WebSocketHandshakeTransport(Protocol):
    """The WSS network boundary; validation and policy stay in the client."""

    def __call__(self, url: str, **options: object): ...


@dataclass(frozen=True)
class PreflightResult:
    """Safe WSS preflight outcome; no exception or response detail is retained."""

    accepted: bool
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.accepted == (self.error_code is not None):
            raise ValueError("accepted preflight must have no error code")

    @property
    def ok(self) -> bool:
        return self.accepted


class BackendPreflightClient:
    """Validate the Worker token with an actual header-only WSS handshake."""

    def __init__(
        self,
        endpoints: BackendEndpointConfig,
        credentials: CredentialInput,
        *,
        connector: WebSocketHandshakeTransport | None = None,
        ssl_context: ssl.SSLContext | None = None,
        open_timeout: float = 10.0,
        logger: SecureEventLogger | None = None,
    ) -> None:
        if ssl_context is not None and (
            ssl_context.verify_mode != ssl.CERT_REQUIRED
            or not ssl_context.check_hostname
        ):
            raise ValueError("TLS context must verify certificates and hostnames")
        if not math.isfinite(open_timeout) or open_timeout <= 0:
            raise ValueError("open_timeout must be finite and positive")
        self._endpoints = endpoints
        self._credentials = credentials
        self._connector = connector or connect
        self._ssl_context = ssl_context
        self._open_timeout = open_timeout
        self._logger = logger

    async def validate(self) -> PreflightResult:
        if not is_valid_worker_id(self._credentials.worker_id):
            return self._failure("INVALID_WORKER_ID")
        if self._endpoints.tls_policy != "REQUIRED":
            return self._failure("INVALID_BACKEND_URL")

        options: dict[str, object] = {
            "additional_headers": {
                "Authorization": f"Bearer {self._credentials.worker_token}"
            },
            "open_timeout": self._open_timeout,
        }
        if self._ssl_context is not None:
            options["ssl"] = self._ssl_context
        try:
            async with self._connector(
                self._endpoints.worker_websocket_url,
                **options,
            ):
                pass
        except InvalidStatus as error:
            return self._failure(_handshake_failure_code(error.response.status_code))
        except Exception:
            return self._failure("BACKEND_UNREACHABLE")

        if self._logger is not None:
            self._logger.event(
                "backend_preflight",
                stage="CREDENTIALS_READY",
                status="READY",
            )
        return PreflightResult(accepted=True)

    def _failure(self, code: str) -> PreflightResult:
        if self._logger is not None:
            self._logger.event(
                "backend_preflight",
                stage="CREDENTIALS_READY",
                status="FAILED",
                errorCode=code,
            )
        return PreflightResult(accepted=False, error_code=code)


def _handshake_failure_code(status_code: int) -> str:
    """Classify a completed response; only a refused upgrade blames the token."""
    if status_code in _TOKEN_REJECTED_STATUSES:
        return "WORKER_TOKEN_REJECTED"
    if status_code in _ENDPOINT_ABSENT_STATUSES:
        return "INVALID_BACKEND_URL"
    return "BACKEND_UNREACHABLE"


class BackendStatusError(RuntimeError):
    """A stable status-client error without raw response or transport detail."""

    def __init__(self, code: str = "BACKEND_STATUS_UNAVAILABLE") -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class StatusTransportResponse:
    status_code: int
    payload: Mapping[str, object] | None

    def __post_init__(self) -> None:
        if isinstance(self.status_code, bool) or not isinstance(self.status_code, int):
            raise ValueError("status_code must be an integer")
        if self.payload is not None and not isinstance(self.payload, Mapping):
            raise ValueError("payload must be an object or null")


class StatusTransport(Protocol):
    async def get_json(
        self,
        url: str,
        headers: dict[str, str],
    ) -> StatusTransportResponse: ...


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuses every redirect so no Authorization header can leave the endpoint."""

    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def _default_opener() -> Callable:
    return urllib.request.build_opener(_NoRedirectHandler).open


class UrllibStatusTransport:
    """Bounded standard-library REST transport kept off the event loop."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        opener: Callable | None = None,
        max_body_bytes: int = _MAX_STATUS_BODY_BYTES,
        allowed_schemes: Collection[str] = ("https",),
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        if isinstance(max_body_bytes, bool) or max_body_bytes <= 0:
            raise ValueError("max_body_bytes must be positive")
        self._timeout_seconds = timeout_seconds
        self._opener = opener or _default_opener()
        self._max_body_bytes = max_body_bytes
        self._allowed_schemes = frozenset(allowed_schemes)

    async def get_json(
        self,
        url: str,
        headers: dict[str, str],
    ) -> StatusTransportResponse:
        return await asyncio.to_thread(self._get_json_sync, url, dict(headers))

    def _get_json_sync(
        self,
        url: str,
        headers: dict[str, str],
    ) -> StatusTransportResponse:
        if urlsplit(url).scheme not in self._allowed_schemes:
            raise BackendStatusError() from None
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with self._opener(request, timeout=self._timeout_seconds) as response:
                status_code = response.status
                raw_payload = response.read(self._max_body_bytes + 1)
        except urllib.error.HTTPError as error:
            with error:
                return StatusTransportResponse(error.code, None)
        except Exception:
            raise BackendStatusError() from None

        if status_code != 200:
            return StatusTransportResponse(status_code, None)
        if len(raw_payload) > self._max_body_bytes:
            raise BackendStatusError() from None
        try:
            payload = json.loads(raw_payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise BackendStatusError() from None
        if not isinstance(payload, Mapping):
            raise BackendStatusError() from None
        return StatusTransportResponse(status_code, payload)


@dataclass(frozen=True)
class WorkerStatusSnapshot:
    """The exact safe subset of the Backend Worker detail response."""

    worker_id: str
    connection_status: str
    worker_status: str
    benchmark_version: str | None
    benchmark_score: float | None
    last_heartbeat: datetime

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "WorkerStatusSnapshot":
        try:
            if not isinstance(payload, Mapping):
                raise ValueError
            worker_id = payload["workerId"]
            connection_status = payload["connectionStatus"]
            worker_status = payload["workerStatus"]
            benchmark = payload["benchmark"]
            last_heartbeat = _parse_timestamp(payload["lastHeartbeat"])

            if not is_valid_worker_id(worker_id):
                raise ValueError
            if (
                not isinstance(connection_status, str)
                or connection_status not in _CONNECTION_STATUSES
            ):
                raise ValueError
            if (
                not isinstance(worker_status, str)
                or worker_status not in _WORKER_STATUSES
            ):
                raise ValueError

            benchmark_version: str | None
            benchmark_score: float | None
            if benchmark is None:
                benchmark_version = None
                benchmark_score = None
            else:
                if not isinstance(benchmark, Mapping):
                    raise ValueError
                version = benchmark["version"]
                score = benchmark["score"]
                if (
                    not isinstance(version, str)
                    or not version.strip()
                    or len(version) > 128
                ):
                    raise ValueError
                if (
                    isinstance(score, bool)
                    or not isinstance(score, (int, float))
                    or not math.isfinite(float(score))
                ):
                    raise ValueError
                benchmark_version = version
                benchmark_score = float(score)
        except (KeyError, TypeError, ValueError, OverflowError):
            raise BackendStatusError() from None

        return cls(
            worker_id=worker_id,
            connection_status=connection_status,
            worker_status=worker_status,
            benchmark_version=benchmark_version,
            benchmark_score=benchmark_score,
            last_heartbeat=last_heartbeat,
        )


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return parsed.astimezone(UTC)


class BackendStatusClient:
    """Fetch the explicit REST Worker detail endpoint under declared auth policy."""

    def __init__(
        self,
        endpoints: BackendEndpointConfig,
        credentials: CredentialInput,
        *,
        transport: StatusTransport | None = None,
        logger: SecureEventLogger | None = None,
    ) -> None:
        self._endpoints = endpoints
        self._credentials = credentials
        self._transport = transport or UrllibStatusTransport()
        self._logger = logger

    async def fetch(self, worker_id: str) -> WorkerStatusSnapshot | None:
        if not is_valid_worker_id(worker_id):
            raise BackendStatusError() from None
        try:
            headers = self._request_headers()
            response = await self._transport.get_json(
                self._endpoints.worker_status_url(worker_id),
                headers,
            )
            if response.status_code == 404:
                return None
            if response.status_code != 200 or response.payload is None:
                raise BackendStatusError()
            return WorkerStatusSnapshot.from_payload(response.payload)
        except Exception:
            if self._logger is not None:
                self._logger.event(
                    "backend_status",
                    stage="REGISTER_VERIFIED",
                    status="FAILED",
                    errorCode="BACKEND_STATUS_UNAVAILABLE",
                )
            raise BackendStatusError() from None

    def _request_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        mode = self._endpoints.status_authentication_mode
        if mode == "NONE":
            return headers
        if mode == "BEARER":
            headers["Authorization"] = f"Bearer {self._credentials.worker_token}"
            return headers
        raise BackendStatusError()


@dataclass(frozen=True)
class BenchmarkEvidence:
    """Safe local subset from the authoritative Worker benchmark cache."""

    gpu_uuid: str
    benchmark_version: str
    benchmark_score: float


class BenchmarkCacheReader(Protocol):
    """The authoritative Worker cache contract Setup only reads."""

    def load(
        self,
        *,
        gpu_uuid: str,
        benchmark_version: str = ...,
    ) -> BenchmarkResult | None: ...


class LocalBenchmarkEvidenceSource:
    """Adapts the authoritative Worker BenchmarkCache; validity rules stay there."""

    def __init__(self, cache: BenchmarkCacheReader) -> None:
        self._cache = cache

    def evidence_for(self, gpu_uuid: str) -> BenchmarkEvidence | None:
        result = self._cache.load(
            gpu_uuid=gpu_uuid,
            benchmark_version=BENCHMARK_VERSION,
        )
        if result is None:
            return None
        return BenchmarkEvidence(
            gpu_uuid=result.gpu_uuid,
            benchmark_version=result.benchmark_version,
            benchmark_score=result.benchmark_score,
        )


@dataclass(frozen=True)
class RegistrationEvidence:
    worker_id: str
    gpu_uuid: str
    benchmark_version: str
    benchmark_score: float
    last_heartbeat: datetime


@dataclass(frozen=True)
class RegistrationBaseline:
    """What the Backend already knew about this Worker before Setup launched it.

    Freshness is decided entirely inside the Backend's own clock domain: either the
    Worker had no record at all, or its heartbeat must move past one the Backend
    itself stamped. The Setup PC's wall clock never takes part, so clock skew
    between the two machines cannot block a genuine registration.
    """

    worker_existed: bool
    last_heartbeat: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.worker_existed, bool):
            raise ValueError("worker_existed must be a boolean")
        if self.last_heartbeat is None:
            return
        if not self.worker_existed:
            raise ValueError("an absent Worker cannot carry a baseline heartbeat")
        if not isinstance(self.last_heartbeat, datetime):
            raise ValueError("baseline heartbeat must be a datetime")
        if (
            self.last_heartbeat.tzinfo is None
            or self.last_heartbeat.utcoffset() is None
        ):
            raise ValueError("baseline heartbeat must include a timezone")
        object.__setattr__(self, "last_heartbeat", self.last_heartbeat.astimezone(UTC))

    @classmethod
    def worker_absent(cls) -> "RegistrationBaseline":
        """No record existed, so any Worker that appears registered just now."""
        return cls(worker_existed=False)

    @classmethod
    def from_heartbeat(cls, last_heartbeat: datetime) -> "RegistrationBaseline":
        """A record existed and the Backend stamped this heartbeat on it.

        The value must come from a Backend response. Passing a Setup-PC timestamp
        here reintroduces the cross-clock comparison this type exists to prevent, so
        callers should reach for capture_registration_baseline instead.
        """
        return cls(worker_existed=True, last_heartbeat=last_heartbeat)

    @classmethod
    def heartbeat_unknown(cls) -> "RegistrationBaseline":
        """A record may exist but no Backend heartbeat could be read for it."""
        return cls(worker_existed=True)

    @classmethod
    def from_snapshot(
        cls,
        snapshot: "WorkerStatusSnapshot | None",
        *,
        expected_worker_id: str,
    ) -> "RegistrationBaseline":
        """Read a baseline from what the Backend returned for expected_worker_id.

        A record for some other Worker is not a usable baseline: adopting its
        heartbeat would set a threshold the expected Worker may never pass.
        """
        if snapshot is None:
            return cls.worker_absent()
        if snapshot.worker_id != expected_worker_id:
            return cls.heartbeat_unknown()
        return cls.from_heartbeat(snapshot.last_heartbeat)


async def capture_registration_baseline(
    status_client: "BackendStatusClient",
    worker_id: str,
) -> RegistrationBaseline:
    """Record the Backend's view of the Worker before Setup starts it.

    A Backend that cannot be read, or that answers with a different Worker's record,
    yields an unknown baseline rather than an error: verification then adopts the
    first heartbeat it observes for the expected Worker and still requires a later
    one, so an already-stale record can never complete Setup.

    A Worker the Backend reports as absent is treated as a new registration, which
    compares no timestamps at all. A Backend that 404s a record it actually holds
    therefore widens acceptance for that run; the Backend is the only authority on
    existence, so Setup cannot second-guess it here.
    """
    try:
        snapshot = await status_client.fetch(worker_id)
    except BackendStatusError:
        return RegistrationBaseline.heartbeat_unknown()
    return RegistrationBaseline.from_snapshot(snapshot, expected_worker_id=worker_id)


class RegistrationVerificationError(RuntimeError):
    """Stable final-verification error without Worker logs or Backend detail."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class Clock(Protocol):
    def now(self) -> datetime: ...

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class BackoffPolicy(Protocol):
    def delay(self, attempt: int) -> float: ...


@dataclass(frozen=True)
class ExponentialBackoffPolicy:
    initial_seconds: float = 1.0
    multiplier: float = 2.0
    cap_seconds: float = 10.0

    def delay(self, attempt: int) -> float:
        if attempt < 0:
            raise ValueError("attempt must be non-negative")
        delay = self.initial_seconds * self.multiplier**attempt
        return min(delay, self.cap_seconds)


class WorkerProcess(Protocol):
    def poll(self) -> int | None: ...


class RegistrationVerifier:
    """Poll until Backend state and authoritative local benchmark evidence agree."""

    def __init__(
        self,
        status_client: BackendStatusClient,
        *,
        expected_worker_id: str,
        baseline: RegistrationBaseline,
        clock: Clock | None = None,
        backoff: BackoffPolicy | None = None,
        timeout_seconds: float = 60.0,
        mismatch_exit_codes: Collection[int] = (),
    ) -> None:
        if not is_valid_worker_id(expected_worker_id):
            raise ValueError("expected_worker_id is invalid")
        if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
            raise ValueError("timeout_seconds must be finite and non-negative")
        if any(
            isinstance(code, bool) or not isinstance(code, int)
            for code in mismatch_exit_codes
        ):
            raise ValueError("mismatch_exit_codes must contain integers")
        self._mismatch_exit_codes = frozenset(mismatch_exit_codes)
        self._status_client = status_client
        self._expected_worker_id = expected_worker_id
        self._clock = clock or SystemClock()
        self._backoff = backoff or ExponentialBackoffPolicy()
        self._timeout_seconds = timeout_seconds
        if not isinstance(baseline, RegistrationBaseline):
            raise ValueError("baseline must be a RegistrationBaseline")
        self._baseline = baseline

    def accepts(
        self,
        status: WorkerStatusSnapshot,
        local: BenchmarkEvidence,
    ) -> bool:
        """Decide one snapshot against the baseline this verifier was built with.

        An unknown baseline is never decidable from a single snapshot, because the
        heartbeat it must advance past is only learned by polling, so this answers
        False and only wait_until_verified can resolve it.
        """
        if self._baseline.worker_existed and self._baseline.last_heartbeat is None:
            return False
        return self._accepts(status, local, self._baseline.last_heartbeat)

    def _accepts(
        self,
        status: WorkerStatusSnapshot,
        local: BenchmarkEvidence,
        baseline_heartbeat: datetime | None,
    ) -> bool:
        """Decide one snapshot. A null baseline means there is nothing to advance past."""
        return (
            self._is_the_expected_online_worker(status)
            and self._heartbeat_is_new(status, baseline_heartbeat)
            and self._benchmark_evidence_agrees(status, local)
        )

    @staticmethod
    def _heartbeat_is_new(
        status: WorkerStatusSnapshot,
        baseline_heartbeat: datetime | None,
    ) -> bool:
        # Both sides of this comparison are stamped by the Backend.
        return baseline_heartbeat is None or status.last_heartbeat > baseline_heartbeat

    def _is_the_expected_online_worker(self, status: WorkerStatusSnapshot) -> bool:
        return (
            status.worker_id == self._expected_worker_id
            and status.connection_status == "ONLINE"
            and status.worker_status == "IDLE"
        )

    def _benchmark_evidence_agrees(
        self,
        status: WorkerStatusSnapshot,
        local: BenchmarkEvidence,
    ) -> bool:
        backend_score = status.benchmark_score
        local_score = local.benchmark_score
        return (
            status.benchmark_version == BENCHMARK_VERSION
            and _positive_finite(backend_score)
            and isinstance(local.gpu_uuid, str)
            and bool(local.gpu_uuid.strip())
            and local.benchmark_version == BENCHMARK_VERSION
            and _positive_finite(local_score)
            and backend_score == local_score
        )

    async def wait_until_verified(
        self,
        worker_id: str,
        benchmark_evidence: BenchmarkEvidence | None,
        process: WorkerProcess,
    ) -> RegistrationEvidence:
        if worker_id != self._expected_worker_id or not is_valid_worker_id(worker_id):
            raise RegistrationVerificationError("REGISTER_TIMEOUT")
        if benchmark_evidence is None:
            raise RegistrationVerificationError("BENCHMARK_REGISTRATION_FAILED")

        deadline = self._clock.now() + timedelta(seconds=self._timeout_seconds)
        attempt = 0
        saw_backend_error = False
        saw_benchmark_failure = False
        # An absent Worker has nothing to advance past; an unknown baseline adopts
        # the first heartbeat the Backend reports and still demands a later one.
        baseline_heartbeat = self._baseline.last_heartbeat
        awaiting_baseline = (
            self._baseline.worker_existed and baseline_heartbeat is None
        )
        while True:
            exit_code = process.poll()
            if exit_code is not None:
                if exit_code in self._mismatch_exit_codes:
                    raise RegistrationVerificationError("WORKER_CREDENTIAL_MISMATCH")
                raise RegistrationVerificationError("WORKER_PROCESS_EXITED")

            # Both diagnoses describe the latest poll only, so one transient blip
            # cannot rename the condition Setup actually gave up on.
            remaining = (deadline - self._clock.now()).total_seconds()
            if remaining > 0:
                try:
                    status = await asyncio.wait_for(
                        self._status_client.fetch(worker_id),
                        timeout=remaining,
                    )
                    saw_backend_error = False
                except Exception:
                    saw_backend_error = True
                    status = None

                saw_benchmark_failure = False
                if status is not None:
                    if awaiting_baseline:
                        if status.worker_id == self._expected_worker_id:
                            baseline_heartbeat = status.last_heartbeat
                            awaiting_baseline = False
                    elif self._accepts(
                        status, benchmark_evidence, baseline_heartbeat
                    ):
                        return RegistrationEvidence(
                            worker_id=status.worker_id,
                            gpu_uuid=benchmark_evidence.gpu_uuid,
                            benchmark_version=status.benchmark_version,
                            benchmark_score=status.benchmark_score,
                            last_heartbeat=status.last_heartbeat,
                        )
                    saw_benchmark_failure = self._is_the_expected_online_worker(
                        status
                    ) and not self._benchmark_evidence_agrees(
                        status, benchmark_evidence
                    )

            now = self._clock.now()
            if now >= deadline:
                if saw_benchmark_failure:
                    code = "BENCHMARK_REGISTRATION_FAILED"
                elif saw_backend_error:
                    code = "BACKEND_STATUS_UNAVAILABLE"
                else:
                    code = "REGISTER_TIMEOUT"
                raise RegistrationVerificationError(code)

            delay = self._backoff.delay(attempt)
            if not math.isfinite(delay) or delay <= 0:
                raise ValueError("backoff delay must be finite and positive")
            await self._clock.sleep(min(delay, (deadline - now).total_seconds()))
            attempt += 1


def _positive_finite(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and float(value) > 0
    )
