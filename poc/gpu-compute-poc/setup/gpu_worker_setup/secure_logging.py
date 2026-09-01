"""A fixed-schema Setup logger that drops and redacts unsafe data."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
from typing import Callable

from .version_policy import WorkerVersion


_ALLOWED_FIELDS = frozenset(
    {"runId", "setupVersion", "stage", "status", "errorCode", "safeExitCode", "diagnosticId"}
)
_SAFE_EVENT_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SAFE_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_SAFE_IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_SAFE_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)
_BEARER_PATTERN = re.compile(r"\bBearer\s+[^\s,;]+", re.IGNORECASE)
_TOKEN_PATTERN = re.compile(r"\btoken\b\s*(?:=|:|\s)\s*[^\s,;]+", re.IGNORECASE)
_TOKEN_ASSIGNMENT_PATTERN = re.compile(
    r"(?P<key>[\"']?(?:worker[_-]?)?token[\"']?)\s*(?P<separator>[:=])\s*(?P<quote>[\"']?)[^\"'\s,;}\]]+",
    re.IGNORECASE,
)


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _redact(value: str) -> str:
    if value.lstrip().startswith(("{", "[")):
        return "[REDACTED]"
    if _TOKEN_ASSIGNMENT_PATTERN.search(value):
        return "[REDACTED]"
    value = _BEARER_PATTERN.sub("Bearer [REDACTED]", value)
    return _TOKEN_PATTERN.sub("token=[REDACTED]", value)


def _safe_string_value(field: str, value: object) -> str | None:
    if not isinstance(value, str):
        return None
    if field in {"runId", "diagnosticId"}:
        valid = bool(_SAFE_UUID.fullmatch(value))
    elif field == "setupVersion":
        if len(value) > 128:
            return None
        try:
            WorkerVersion.parse(value)
        except ValueError:
            return None
        valid = True
    else:
        valid = bool(_SAFE_IDENTIFIER.fullmatch(value))
    if not valid or _redact(value) != value:
        return None
    return value


def _safe_exit_code(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not 0 <= value <= 2**31 - 1:
        return None
    return value


class SecureEventLogger:
    """Writes newline-delimited JSON events with a closed field allowlist."""

    def __init__(
        self,
        log_path: str | Path,
        *,
        rotation_bytes: int = 1_048_576,
        timestamp_factory: Callable[[], str] = _utc_timestamp,
    ) -> None:
        if rotation_bytes <= 0:
            raise ValueError("rotation_bytes must be positive")
        self._log_path = Path(log_path)
        self._rotation_bytes = rotation_bytes
        self._timestamp_factory = timestamp_factory

    def event(self, name: str, **fields: object) -> None:
        timestamp = self._timestamp_factory()
        record: dict[str, object] = {
            "timestamp": timestamp
            if isinstance(timestamp, str)
            and _SAFE_TIMESTAMP.fullmatch(timestamp)
            and _redact(timestamp) == timestamp
            else "invalid_timestamp",
            "event": name if isinstance(name, str) and _SAFE_EVENT_NAME.fullmatch(name) else "invalid_event",
        }
        for key, value in fields.items():
            if key not in _ALLOWED_FIELDS:
                continue
            if key == "safeExitCode":
                safe_exit_code = _safe_exit_code(value)
                if safe_exit_code is not None:
                    record[key] = safe_exit_code
                continue
            safe_value = _safe_string_value(key, value)
            if safe_value is not None:
                record[key] = safe_value

        encoded = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        if self._log_path.exists() and self._log_path.stat().st_size > 0:
            if self._log_path.stat().st_size + len(encoded) > self._rotation_bytes:
                os.replace(self._log_path, self._log_path.with_name(f"{self._log_path.name}.1"))
        with self._log_path.open("ab") as stream:
            stream.write(encoded)
