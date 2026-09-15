"""The Backend's Worker API, as finally agreed.

Three calls, and what authenticates each one:

* ``POST /api/workers/register`` -- ``Bearer <user access token>``. The person
  is signed in; the Backend decides from that token whose account the machine
  joins. The Worker never sends a user id: a client that names its own owner
  is a client that can name somebody else's.
* ``POST /api/workers/authenticate`` -- ``Bearer <worker token>``. From here on
  the machine speaks for itself and the person is not involved.
* ``PUT /api/workers/{workerId}/gpu`` -- ``Bearer <worker token>``. Answers 204.

The state is read from the **HTTP status**, not from a string in the body:

* 200 -- REGISTERED
* 401 -- AUTH_FAILED, the token was refused
* 403 -- REVOKED, the account no longer wants this Worker
* 404 -- see below; **never** REVOKED on its own
* 5xx -- BACKEND_UNREACHABLE, the service failed to answer the question
* a network error or timeout -- BACKEND_UNREACHABLE

**A 404 is not evidence about this Worker.** It is what a missing Worker record
answers, but it is equally what an endpoint that does not exist yet answers, or
a path spelt wrong -- which is exactly this build's situation. So the body is
read: only ``code`` being ``WORKER_NOT_FOUND`` means the service actually
looked and found nothing, and that is reported as its own state. Every other
404 is a contract error -- the service did not answer the question that was
asked. Neither one deletes a credential, enrols the machine again, or concludes
that it was revoked.

A 5xx is deliberately *not* an auth failure either. The service breaking
teaches nothing about this Worker, and treating it as a refusal would tell
somebody with a perfectly good machine to register it again.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from .backend_client import BackendAnswer, BackendClient, Outcome
from .logging_setup import LOGGER_NAME
from .worker_auth import RegistryAnswer, WorkerAuthState
from .worker_credentials import WorkerCredentials

_debug = logging.getLogger(LOGGER_NAME)

#: The one 404 body that means the service really did look for this Worker.
WORKER_NOT_FOUND = "WORKER_NOT_FOUND"

#: The one 401 body that means the stored Worker token is dead -- what the
#: Backend answers once the Worker has been deleted from the account.
INVALID_WORKER_TOKEN = "INVALID_WORKER_TOKEN"

REGISTER_PATH = "/api/workers/register"
AUTHENTICATE_PATH = "/api/workers/authenticate"

#: quote() with nothing left safe -- a worker id goes in a path segment.
SAFE = ""


def benchmark_path(worker_id: str) -> str:
    """Where a re-measured benchmark goes.

    Replaces ``PUT .../gpu``. What this updates is a measurement, and it now
    carries the benchmark's version so a number produced by an older workload
    is never compared against one from a newer workload.
    """
    return f"/api/workers/{quote(worker_id, safe=SAFE)}/benchmark"


def operator_path(worker_id: str) -> str:
    """Where this machine's public signing address goes.

    Used to attach an operator address to a Worker registered before operator
    keys existed. **Only the address.** The key behind it never leaves this PC.
    """
    return f"/api/workers/{quote(worker_id, safe=SAFE)}/operator"


@dataclass(frozen=True)
class BenchmarkUpdate:
    """A re-measured GPU, as ``PUT .../benchmark`` states it.

    ``driverVersion`` is deliberately **not** here. It is not part of this
    contract: the Scheduler plans with the GPU model, its VRAM and the measured
    throughput, and a driver string is neither an input to that nor something
    the Backend keeps.
    """

    gpu_model: str
    vram_mb: int
    #: Measured, never assumed. The one number the Scheduler plans with.
    benchmark_tokens_per_sec: float
    #: Which fixed workload produced that number. Results from two benchmark
    #: versions are not comparable, and a Scheduler ranking across them would
    #: be ranking on the wrong thing.
    benchmark_version: str

    def as_payload(self):
        return {
            "gpuModel": self.gpu_model,
            "vramMb": int(self.vram_mb),
            "benchmarkTokensPerSec": float(self.benchmark_tokens_per_sec),
            "benchmarkVersion": self.benchmark_version,
        }


@dataclass(frozen=True)
class WorkerRegistrationRequest:
    """What ``POST /api/workers/register`` is told about this machine.

    Grouped rather than flat -- ``system``, ``gpu``, ``benchmark`` -- because
    the three answer different questions and change on different schedules. The
    benchmark is re-measured; the GPU changes when somebody swaps a card; the
    operating system rarely changes at all.

    ``operatorAddress`` is **required**. A Worker registered without one cannot
    be approved on chain, so it would be scheduled work it could never accept.
    The address is public; the key behind it is made on this PC, sealed with
    DPAPI, and never sent anywhere.
    """

    operator_address: str
    client_version: str
    os_version: str
    wsl_available: bool
    gpu_model: str
    vram_mb: int
    benchmark_tokens_per_sec: float
    benchmark_version: str

    @classmethod
    def describing(
        cls,
        facts,
        benchmark_tokens_per_sec: float,
        *,
        operator_address: str,
        client_version: str,
        benchmark_version: str,
        wsl_available: bool = True,
    ) -> "WorkerRegistrationRequest":
        """This machine, plus what has to be measured or made rather than read."""
        return cls(
            operator_address=operator_address,
            client_version=client_version,
            os_version=facts.os_version,
            wsl_available=wsl_available,
            gpu_model=facts.gpu_model,
            vram_mb=facts.vram_mb,
            benchmark_tokens_per_sec=benchmark_tokens_per_sec,
            benchmark_version=benchmark_version,
        )

    def as_payload(self):
        return {
            "operatorAddress": self.operator_address,
            "clientVersion": self.client_version,
            "system": {
                "os": self.os_version,
                "wslAvailable": bool(self.wsl_available),
            },
            "gpu": {
                "gpuModel": self.gpu_model,
                "vramMb": int(self.vram_mb),
            },
            "benchmark": {
                "benchmarkTokensPerSec": float(self.benchmark_tokens_per_sec),
                "benchmarkVersion": self.benchmark_version,
            },
        }

    def benchmark_update(self) -> BenchmarkUpdate:
        """The same measurement, for the endpoint that refreshes it later."""
        return BenchmarkUpdate(
            self.gpu_model, self.vram_mb,
            self.benchmark_tokens_per_sec, self.benchmark_version,
        )


class HttpWorkerRegistry:
    """The Backend's Worker side over HTTP.

    Satisfies :class:`gshared_worker.worker_auth.WorkerRegistry`. Every request
    goes through :class:`~gshared_worker.backend_client.BackendClient`, so
    timeouts, unreachability and the ``{code, message}`` error body are handled
    once for the whole Worker rather than twice.
    """

    def __init__(self, client: BackendClient) -> None:
        self._client = client

    def verify(self, credentials: WorkerCredentials) -> RegistryAnswer:
        """Does the Backend still accept this Worker. Changes nothing."""
        # Both identifiers travel in the body. This endpoint is the one place
        # the Worker token is **not** an Authorization header: here it is the
        # thing being checked, not the thing authorising the request.
        answered = self._client.request(
            "POST", AUTHENTICATE_PATH,
            {"workerId": credentials.worker_id,
             "workerToken": credentials.worker_token},
        )
        if not answered.succeeded:
            if answered.status == 401 and answered.code == INVALID_WORKER_TOKEN:
                # Only here, and only with this code, is the stored identity
                # known to be dead. A 401 on registration is about the person's
                # access token, and a 401 without the code says nothing certain.
                _debug.warning("worker api: 401 %s", INVALID_WORKER_TOKEN)
                return RegistryAnswer(
                    WorkerAuthState.CREDENTIALS_INVALIDATED,
                    "the service no longer accepts the stored Worker token")
            return _refused(answered, "this Worker")

        named = (answered.body or {}).get("workerId")
        if isinstance(named, str) and named and named != credentials.worker_id:
            # The service accepted the token but says it belongs to a different
            # Worker. Carrying on would run work under the wrong identity.
            return RegistryAnswer(
                WorkerAuthState.AUTH_FAILED,
                "the service accepted the token for a different Worker",
            )
        return RegistryAnswer(
            WorkerAuthState.REGISTERED, "the service still accepts this Worker")

    def register(
        self, access_token: str, registration: WorkerRegistrationRequest
    ) -> RegistryAnswer:
        """Attach this machine to the account that access token belongs to."""
        answered = self._client.request(
            "POST", REGISTER_PATH, registration.as_payload(), bearer=access_token)
        if not answered.succeeded:
            return _refused(answered, "this machine")

        body = answered.body or {}
        worker_id, token = body.get("workerId"), body.get("workerToken")
        if not _given(worker_id) or not _given(token):
            # Registered, apparently, without saying what this Worker now is.
            # The service turns this into a failure; nothing is stored.
            return RegistryAnswer(
                WorkerAuthState.REGISTERED,
                "the service registered this machine but returned no identity",
            )
        return RegistryAnswer(
            WorkerAuthState.REGISTERED, "this machine is registered",
            WorkerCredentials(worker_id.strip(), token.strip()),
        )

    def update_benchmark(
        self, credentials: WorkerCredentials, benchmark: BenchmarkUpdate
    ) -> RegistryAnswer:
        """Refresh what this Worker measured. Accepted means 204.

        Replaces the old ``PUT .../gpu``.
        """
        answered = self._client.request(
            "PUT", benchmark_path(credentials.worker_id), benchmark.as_payload(),
            bearer=credentials.worker_token,
        )
        if not answered.succeeded:
            return _refused(answered, "this Worker")
        return RegistryAnswer(
            WorkerAuthState.REGISTERED, "the benchmark was accepted")

    def register_operator(
        self, credentials: WorkerCredentials, operator_address: str
    ) -> RegistryAnswer:
        """Attach a public signing address to a Worker that has none.

        For installations registered before operator keys existed. **Only the
        address**; the private key stays sealed on this PC.

        Nothing here decides whether attaching it is safe. That is
        :func:`gshared_worker.worker_wallet.plan_operator_migration`, which
        refuses when the Backend already knows a different address -- silently
        overwriting one would break every job the old key had accepted, with
        no way to finish them.
        """
        answered = self._client.request(
            "PUT", operator_path(credentials.worker_id),
            {"operatorAddress": operator_address},
            bearer=credentials.worker_token,
        )
        if not answered.succeeded:
            return _refused(answered, "this Worker")
        return RegistryAnswer(
            WorkerAuthState.REGISTERED, "the operator address was accepted")


def _given(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _refused(answered: BackendAnswer, subject: str) -> RegistryAnswer:
    """Turn a status into a state. Nothing is learned from an outage."""
    if answered.outcome is Outcome.UNREACHABLE:
        return RegistryAnswer(WorkerAuthState.BACKEND_UNREACHABLE, answered.message)

    status = answered.status
    if status is not None and status >= 500:
        return RegistryAnswer(
            WorkerAuthState.BACKEND_UNREACHABLE,
            "G-shared 서비스에 문제가 생겨 응답하지 못했습니다. "
            "아무것도 바뀌지 않았습니다. 서비스가 복구되면 다시 시도하세요.",
        )
    if status == 403:
        return RegistryAnswer(
            WorkerAuthState.REVOKED, f"the account no longer accepts {subject}")
    if status == 404:
        if answered.code == WORKER_NOT_FOUND:
            # The service looked and found nothing. Said so in as many words,
            # which is the only thing that makes a 404 mean this.
            _debug.warning("worker api: 404 %s", WORKER_NOT_FOUND)
            return RegistryAnswer(
                WorkerAuthState.WORKER_NOT_FOUND,
                f"the service has no record of {subject}")
        # A 404 from a path that is not there. Reading this as a revocation
        # would tell a supplier their machine was removed from their account
        # because an endpoint has not been written yet.
        _debug.warning("worker api: 404 without %s -- treating as a contract error "
                    "(code=%s)", WORKER_NOT_FOUND, answered.code)
        return RegistryAnswer(
            WorkerAuthState.BACKEND_CONTRACT_ERROR,
            "the service answered 404 at that address, which says nothing "
            f"about {subject}")
    return RegistryAnswer(
        WorkerAuthState.AUTH_FAILED,
        answered.message or f"the service refused {subject}")
