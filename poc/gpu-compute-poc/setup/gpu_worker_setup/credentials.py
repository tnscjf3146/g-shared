"""Credential acquisition and atomic protected Worker config writes."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Protocol

from gpu_worker.agent_config import DEFAULT_SUPPORTED_WORKLOADS
from gpu_worker.protected_credentials import (
    protect_current_user,
    unprotect_current_user,
)

from .defaults import DEFAULT_GPU_INDEX, DEFAULT_HOURLY_PRICE
from .endpoint_config import BackendEndpointConfig
from .state_store import SetupStateStore, atomic_json_replace


_COMPLETION_STAGES = ("REGISTER_VERIFIED", "SETUP_COMPLETED")
_TOKEN_FIELDS = ("workerTokenProtected", "workerToken")


@dataclass(frozen=True)
class CredentialInput:
    """The three supplier-provided values retained only in memory."""

    backend_url: str
    worker_id: str
    worker_token: str

    def __post_init__(self) -> None:
        for name in ("backend_url", "worker_id", "worker_token"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
            if name == "worker_token" and value != value.strip():
                raise ValueError("worker_token must not contain surrounding whitespace")


class CredentialProvider(Protocol):
    def acquire(self) -> CredentialInput: ...


class ManualCredentialProvider:
    """Provides the current UI submission without persisting it."""

    def __init__(self, credentials: CredentialInput) -> None:
        self._credentials = credentials

    def acquire(self) -> CredentialInput:
        return self._credentials


class CredentialProtector(Protocol):
    def protect(self, secret: str) -> str: ...

    def unprotect(self, encoded: str) -> str: ...


class CurrentUserCredentialProtector:
    def protect(self, secret: str) -> str:
        return protect_current_user(secret)

    def unprotect(self, encoded: str) -> str:
        return unprotect_current_user(encoded)


class WorkerConfigWriter:
    """Atomically writes the existing Worker schema with only protected credentials."""

    def __init__(
        self,
        config_path: str | Path,
        *,
        protector: CredentialProtector | None = None,
    ) -> None:
        self._config_path = Path(config_path)
        self._protector = protector or CurrentUserCredentialProtector()

    def write(
        self,
        credentials: CredentialInput,
        endpoints: BackendEndpointConfig,
    ) -> None:
        protected_value = self._protector.protect(credentials.worker_token)
        payload = {
            "backendUrl": endpoints.worker_websocket_url,
            "workerId": credentials.worker_id,
            "workerTokenProtected": protected_value,
            "hourlyPrice": DEFAULT_HOURLY_PRICE,
            "gpuIndex": DEFAULT_GPU_INDEX,
            "supportedWorkloads": list(DEFAULT_SUPPORTED_WORKLOADS),
        }
        self._atomic_replace(payload)

    def _atomic_replace(self, payload: dict[str, object]) -> None:
        atomic_json_replace(self._config_path, payload)


@dataclass(frozen=True)
class CredentialMismatchOutcome:
    """What survives a mismatch: the endpoint and identity, never the Token."""

    code: str
    backend_url: str | None
    worker_id: str | None
    worker_token: str = ""


class CredentialMismatchReset:
    """Clears the rejected Token and completion without discarding environment work.

    Completion stages are cleared before the Token so an interrupted reset can only
    leave Setup needing another verification pass, never a completed Setup holding a
    Token the Backend rejected. Applying it twice is safe.
    """

    def __init__(
        self,
        config_path: str | Path,
        state_store: SetupStateStore,
    ) -> None:
        self._config_path = Path(config_path)
        self._state_store = state_store

    def apply(self) -> CredentialMismatchOutcome:
        self._clear_completion_stages()
        return CredentialMismatchOutcome(
            code="WORKER_CREDENTIAL_MISMATCH",
            **self._clear_protected_token(),
        )

    def _clear_completion_stages(self) -> None:
        state = self._state_store.load()
        completed = [
            stage
            for stage in state.get("completedStages", [])
            if stage not in _COMPLETION_STAGES
        ]
        self._state_store.save({**state, "completedStages": completed})

    def _clear_protected_token(self) -> dict[str, str | None]:
        try:
            raw = self._config_path.read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            return {"backend_url": None, "worker_id": None}

        payload = _parse_config_object(raw)
        if payload is None:
            # An unreadable config may still hold the rejected Token, and nothing in
            # it can be preserved, so the whole file goes.
            self._config_path.unlink(missing_ok=True)
            return {"backend_url": None, "worker_id": None}

        remaining = {
            key: value for key, value in payload.items() if key not in _TOKEN_FIELDS
        }
        if remaining != payload:
            atomic_json_replace(self._config_path, remaining)
        return {
            "backend_url": _optional_string(remaining.get("backendUrl")),
            "worker_id": _optional_string(remaining.get("workerId")),
        }


def _parse_config_object(raw: str) -> dict[str, object] | None:
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
