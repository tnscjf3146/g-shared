"""Executable-directory configuration for the standalone Windows Worker agent.

The GPU supplier runs `gpu-worker-agent.exe` by double-clicking it, so settings
are read from `worker-config.json` in the directory that holds the running
agent. The current working directory is never used because Explorer and shell
shortcuts do not set it to the agent directory. Command-line arguments and
environment variables keep working and take precedence over the file.
"""

from __future__ import annotations

import json
import sys
from argparse import Namespace
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .errors import WorkerConfigurationError
from .protected_credentials import unprotect_current_user

CONFIG_FILE_NAME = "worker-config.json"
EXAMPLE_FILE_NAME = "worker-config.example.json"

DEFAULT_GPU_INDEX = 0
DEFAULT_SUPPORTED_WORKLOADS: tuple[str, ...] = ("EMBEDDING",)

ENV_BACKEND_URL = "GPU_WORKER_BACKEND_URL"
ENV_WORKER_ID = "GPU_WORKER_ID"
ENV_WORKER_TOKEN = "GPU_WORKER_TOKEN"
ENV_HOURLY_PRICE = "GPU_WORKER_HOURLY_PRICE"
ENV_GPU_INDEX = "GPU_WORKER_GPU_INDEX"
ENV_SUPPORTED_WORKLOADS = "GPU_WORKER_SUPPORTED_WORKLOADS"

_STRING_KEYS = ("backendUrl", "workerId", "workerToken")
_INTEGER_KEYS = ("hourlyPrice", "gpuIndex")
_LIST_KEYS = ("supportedWorkloads",)
_KNOWN_KEYS = _STRING_KEYS + ("workerTokenProtected",) + _INTEGER_KEYS + _LIST_KEYS


@dataclass(frozen=True)
class AgentSettings:
    backend_url: str
    worker_id: str
    hourly_price: int
    gpu_index: int
    supported_workloads: tuple[str, ...]
    worker_token: str | None
    config_path: Path | None


def agent_directory() -> Path:
    """Return the directory holding the agent, independent of the working dir.

    In a PyInstaller bundle `sys.executable` is the agent executable itself, so
    its parent is the directory the supplier copied. `sys._MEIPASS` is not used:
    for a one-file bundle it points at a temporary extraction directory, which
    never contains the supplier's configuration file.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def config_file_path(directory: Path | None = None) -> Path:
    return (directory or agent_directory()) / CONFIG_FILE_NAME


def read_config_file(path: Path) -> dict[str, object]:
    """Read and validate the optional configuration file.

    A missing file is not an error; the agent may be driven entirely by
    command-line arguments or environment variables.
    """
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise WorkerConfigurationError(
            f"{CONFIG_FILE_NAME} at {path} could not be read: {error.strerror or error}"
        ) from error
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise WorkerConfigurationError(
            f"{CONFIG_FILE_NAME} at {path} is not valid JSON "
            f"(line {error.lineno}, column {error.colno})"
        ) from error
    if not isinstance(payload, dict):
        raise WorkerConfigurationError(
            f"{CONFIG_FILE_NAME} at {path} must contain a JSON object"
        )

    values = {key: value for key, value in payload.items() if not key.startswith("_")}
    unknown = sorted(key for key in values if key not in _KNOWN_KEYS)
    if unknown:
        raise WorkerConfigurationError(
            f"{CONFIG_FILE_NAME} at {path} has unsupported setting(s): "
            f"{', '.join(unknown)}. Supported settings: {', '.join(_KNOWN_KEYS)}"
        )

    for key in _STRING_KEYS:
        if key in values and not isinstance(values[key], str):
            raise WorkerConfigurationError(
                f"{CONFIG_FILE_NAME} at {path}: {key} must be a string"
            )
    for key in _INTEGER_KEYS:
        if key in values and (
            isinstance(values[key], bool) or not isinstance(values[key], int)
        ):
            raise WorkerConfigurationError(
                f"{CONFIG_FILE_NAME} at {path}: {key} must be an integer"
            )
    for key in _LIST_KEYS:
        value = values.get(key)
        if key in values and (
            not isinstance(value, list)
            or not all(isinstance(item, str) for item in value)
        ):
            raise WorkerConfigurationError(
                f"{CONFIG_FILE_NAME} at {path}: {key} must be a list of strings"
            )
    return values


def resolve_settings(
    args: Namespace,
    *,
    env: Mapping[str, str],
    directory: Path | None = None,
    token_unprotector: Callable[[str], str] | None = None,
) -> AgentSettings:
    """Merge command-line arguments, environment variables and the config file."""
    path = config_file_path(directory)
    file_values = read_config_file(path)
    config_path = path if path.is_file() else None

    backend_url = _first(
        _clean(getattr(args, "backend_url", None)),
        _clean(env.get(ENV_BACKEND_URL)),
        _clean(file_values.get("backendUrl")),
    )
    worker_id = _first(
        _clean(getattr(args, "worker_id", None)),
        _clean(env.get(ENV_WORKER_ID)),
        _clean(file_values.get("workerId")),
    )
    worker_token = _resolve_worker_token(
        env,
        file_values,
        token_unprotector=token_unprotector,
    )
    hourly_price = _first(
        getattr(args, "hourly_price", None),
        _env_integer(env, ENV_HOURLY_PRICE),
        file_values.get("hourlyPrice"),
    )
    gpu_index = _first(
        getattr(args, "gpu_index", None),
        _env_integer(env, ENV_GPU_INDEX),
        file_values.get("gpuIndex"),
        DEFAULT_GPU_INDEX,
    )
    supported_workloads = _first(
        _workloads(getattr(args, "supported_workloads", None)),
        _workloads(_split(env.get(ENV_SUPPORTED_WORKLOADS))),
        _workloads(file_values.get("supportedWorkloads")),
        DEFAULT_SUPPORTED_WORKLOADS,
    )

    missing = [
        key
        for key, value in (
            ("backendUrl", backend_url),
            ("workerId", worker_id),
            ("hourlyPrice", hourly_price),
        )
        if value is None
    ]
    if missing:
        raise WorkerConfigurationError(_missing_message(missing, path, config_path))

    return AgentSettings(
        backend_url=backend_url,
        worker_id=worker_id,
        hourly_price=hourly_price,
        gpu_index=gpu_index,
        supported_workloads=supported_workloads,
        worker_token=worker_token,
        config_path=config_path,
    )


def _missing_message(
    missing: list[str], path: Path, config_path: Path | None
) -> str:
    if config_path is None and missing == ["backendUrl", "workerId", "hourlyPrice"]:
        return (
            f"no configuration was given. {CONFIG_FILE_NAME} was not found at {path} "
            "and no command-line arguments were provided. Copy "
            f"{EXAMPLE_FILE_NAME} to {CONFIG_FILE_NAME} next to the agent "
            "executable and fill in your settings."
        )
    location = (
        f"Set them in {config_path}"
        if config_path is not None
        else f"Create {path} from {EXAMPLE_FILE_NAME}"
    )
    return (
        "the Worker configuration is incomplete. Missing required setting(s): "
        f"{', '.join(missing)}. {location}, or pass the matching command-line "
        "arguments."
    )


def _first(*candidates: object) -> object:
    for candidate in candidates:
        if candidate is not None:
            return candidate
    return None


def _resolve_worker_token(
    env: Mapping[str, str],
    file_values: Mapping[str, object],
    *,
    token_unprotector: Callable[[str], str] | None,
) -> str | None:
    environment_value = _clean(env.get(ENV_WORKER_TOKEN))
    if environment_value is not None:
        return environment_value

    if "workerTokenProtected" in file_values:
        protected_value = file_values["workerTokenProtected"]
        if not isinstance(protected_value, str):
            raise WorkerConfigurationError(
                "CREDENTIAL_DECRYPTION_FAILED: protected credential is invalid"
            )
        try:
            unprotected = (token_unprotector or unprotect_current_user)(protected_value)
        except Exception:
            raise WorkerConfigurationError(
                "CREDENTIAL_DECRYPTION_FAILED: protected credential could not be decrypted"
            ) from None
        cleaned = _clean(unprotected)
        if cleaned is None:
            raise WorkerConfigurationError(
                "CREDENTIAL_DECRYPTION_FAILED: protected credential is empty"
            )
        return cleaned

    return _clean(file_values.get("workerToken"))


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _split(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [item.strip() for item in value.split(",")]


def _workloads(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, (list, tuple)):
        return None
    items = tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
    return items or None


def _env_integer(env: Mapping[str, str], name: str) -> int | None:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw.strip())
    except ValueError as error:
        raise WorkerConfigurationError(
            f"{name} must be an integer, found {raw!r}"
        ) from error
