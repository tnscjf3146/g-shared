"""Atomic, secret-free persistence for Setup state and update journals."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping


STATE_SCHEMA_VERSION = 1
_STATE_FIELDS = frozenset(
    {
        "schemaVersion",
        "completedStages",
        "setupVersion",
        "workerVersion",
        "lastSafeError",
        "warnings",
        "dockerLicenseAccepted",
        "dockerLicenseAcceptedAt",
        "autoStartEnabled",
    }
)
_SETUP_STAGES = frozenset(
    {
        "CREDENTIALS_READY",
        "SYSTEM_READY",
        "WSL_READY",
        "DOCKER_READY",
        "WORKER_INSTALLED",
        "BENCHMARK_READY",
        "REGISTER_VERIFIED",
        "SETUP_COMPLETED",
    }
)
_SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SAFE_ERROR_STAGE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_SAFE_DIAGNOSTIC_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def atomic_json_replace(path: Path, value: Mapping[str, Any]) -> None:
    """Write one JSON object durably, never exposing a partial destination file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}-", suffix=".tmp"
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _load_json_object(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("Setup persistence must contain a JSON object")
    return value


def _is_non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value)


class SetupStateStore:
    """Reads and writes only the Setup state fields safe to retain locally."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        state = _load_json_object(self.path)
        if state is None:
            return {"schemaVersion": STATE_SCHEMA_VERSION, "completedStages": []}
        self._validate(state)
        return state

    def save(self, state: Mapping[str, Any]) -> None:
        candidate = dict(state)
        self._validate(candidate)
        atomic_json_replace(self.path, candidate)

    @staticmethod
    def _validate(state: Mapping[str, Any]) -> None:
        unknown_fields = set(state).difference(_STATE_FIELDS)
        if unknown_fields:
            raise ValueError("Setup state contains fields that are not safe to persist")
        if state.get("schemaVersion") != STATE_SCHEMA_VERSION:
            raise ValueError("Setup state schemaVersion is unsupported")

        completed_stages = state.get("completedStages", [])
        if not isinstance(completed_stages, list) or any(
            stage not in _SETUP_STAGES for stage in completed_stages
        ):
            raise ValueError("completedStages must contain only known Setup stages")

        for field in ("setupVersion", "workerVersion", "dockerLicenseAcceptedAt"):
            if field in state and not _is_non_empty_string(state[field]):
                raise ValueError(f"{field} must be a non-empty string")

        for field in ("dockerLicenseAccepted", "autoStartEnabled"):
            if field in state and not isinstance(state[field], bool):
                raise ValueError(f"{field} must be a boolean")

        warnings = state.get("warnings", [])
        if not isinstance(warnings, list) or any(
            not isinstance(warning, str) or not _SAFE_CODE.fullmatch(warning)
            for warning in warnings
        ):
            raise ValueError("warnings must contain only safe error codes")

        last_safe_error = state.get("lastSafeError")
        if last_safe_error is not None:
            if not isinstance(last_safe_error, Mapping):
                raise ValueError("lastSafeError must be a safe error object")
            if set(last_safe_error).difference({"stage", "code", "diagnosticId"}):
                raise ValueError("lastSafeError contains an unsafe field")
            if not isinstance(last_safe_error.get("stage"), str) or not _SAFE_ERROR_STAGE.fullmatch(
                last_safe_error["stage"]
            ):
                raise ValueError("lastSafeError.stage must be a safe error-stage identifier")
            if not isinstance(last_safe_error.get("code"), str) or not _SAFE_CODE.fullmatch(
                last_safe_error["code"]
            ):
                raise ValueError("lastSafeError.code must be a safe error code")
            if not isinstance(last_safe_error.get("diagnosticId"), str) or not _SAFE_DIAGNOSTIC_ID.fullmatch(
                last_safe_error["diagnosticId"]
            ):
                raise ValueError("lastSafeError.diagnosticId must be a canonical opaque UUID")


class UpdateJournalStore:
    """Atomic storage for the transaction owner to persist its update journal."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any] | None:
        return _load_json_object(self.path)

    def save(self, journal: Mapping[str, Any]) -> None:
        atomic_json_replace(self.path, dict(journal))

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
