"""Where the Worker's settings come from, and in what order.

A supplier double-clicks the executable, so the current working directory is
whatever Explorer felt like and is never used. Settings are read from
``worker-config.json`` in the directory that holds the running program, and the
environment overrides that file so a second environment or a Backend on
someone's own machine needs no edit to a supplier's file.

Precedence, highest first: environment variable, configuration file, built-in
default. The PoC also accepted command-line arguments ahead of all three; that
parser belongs to the Worker client that has not been ported yet, so it is not
here.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .credentials import unprotect_current_user
from .errors import WorkerConfigurationError

CONFIG_FILE_NAME = "worker-config.json"
EXAMPLE_FILE_NAME = "worker-config.example.json"

#: Which GPU this machine offers, when nobody has said otherwise.
DEFAULT_GPU_INDEX = 0

#: What an hour of this GPU costs, when nobody has said otherwise.
#:
#: Enrolment gives a machine an identity and a token; it does not give it a
#: price, and a supplier handed an executable was never asked for one. Without a
#: default the Worker refuses to connect at the very last step.
DEFAULT_HOURLY_PRICE = 600

#: Where the service lives, when nobody has said otherwise.
#:
#: The deployed G-shared service. A supplier runs this program on their own PC
#: and has no reason to know an address, so the default has to be the real one
#: rather than a developer's machine. ``backendUrl`` in the configuration file,
#: or GSHARED_WORKER_BACKEND_URL, points a build somewhere else -- at a local
#: Backend during development, say.
#:
#: The scheme is ``wss``, so the HTTP origin derived from it for signing in is
#: ``https``. See ``_http_origin_of`` in :mod:`gshared_worker.auth_check`: one
#: setting decides both, and they cannot drift apart.
DEFAULT_BACKEND_URL = "wss://j15e204.p.ssafy.io/ws/workers"

#: What a machine offers to do, when nobody has said otherwise.
#:
#: Empty on purpose. In the PoC this was taken from the container image
#: allowlist so the advertisement and the ability could not drift apart; that
#: allowlist belongs to job execution and arrives with it. Until then a machine
#: advertises nothing rather than advertising work it cannot do.
DEFAULT_SUPPORTED_WORKLOADS: tuple[str, ...] = ()

#: The WSL2 distribution the Worker creates and is alone responsible for.
#:
#: The environment-preparation code that acts on it is not part of this build.
#: The name lives here because it is a setting of the Worker rather than of any
#: one step, and because nothing may act on a distribution this constant did not
#: name.
WSL_DISTRO_NAME = "gshared-worker"

ENV_BACKEND_URL = "GSHARED_WORKER_BACKEND_URL"
ENV_WORKER_ID = "GSHARED_WORKER_ID"
ENV_WORKER_TOKEN = "GSHARED_WORKER_TOKEN"
ENV_HOURLY_PRICE = "GSHARED_WORKER_HOURLY_PRICE"
ENV_GPU_INDEX = "GSHARED_WORKER_GPU_INDEX"
ENV_SUPPORTED_WORKLOADS = "GSHARED_WORKER_SUPPORTED_WORKLOADS"

_STRING_KEYS = ("backendUrl", "workerId", "workerToken")
_INTEGER_KEYS = ("hourlyPrice", "gpuIndex")
_LIST_KEYS = ("supportedWorkloads",)
_KNOWN_KEYS = _STRING_KEYS + ("workerTokenProtected",) + _INTEGER_KEYS + _LIST_KEYS


@dataclass(frozen=True)
class WorkerSettings:
    backend_url: str
    worker_id: str
    hourly_price: int
    gpu_index: int
    supported_workloads: tuple[str, ...]
    worker_token: str | None
    config_path: Path | None


def worker_directory() -> Path:
    """The directory holding the Worker, independent of the working directory.

    In a PyInstaller bundle ``sys.executable`` is the Worker executable itself,
    so its parent is the directory the supplier copied. ``sys._MEIPASS`` is not
    used: for a one-file bundle it points at a temporary extraction directory,
    which never holds the supplier's configuration file.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def config_file_path(directory: Path | None = None) -> Path:
    """Where the settings and the stored credentials live, in one file."""
    return (directory or worker_directory()) / CONFIG_FILE_NAME


def read_config_file(path: Path) -> dict[str, object]:
    """Read and validate the optional configuration file.

    A missing file is not an error; the Worker may be driven entirely by
    environment variables. A file that is there and wrong is an error, said
    plainly, because a typo silently ignored is a machine running with a setting
    its owner believes they changed.
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


def resolve_backend_url(
    *, env: Mapping[str, str], directory: Path | None = None
) -> str:
    """Which G-shared service to talk to, without needing an enrolled machine.

    The sign-in window needs this and nothing else: it exists to obtain the
    workerId that :func:`resolve_settings` insists on, so it cannot be made to
    ask for one first. The configuration file is still read and still refused
    if it is malformed -- a typo is a typo whoever is asking.
    """
    return _backend_url(env, read_config_file(config_file_path(directory)))


def _backend_url(env: Mapping[str, str], file_values: Mapping[str, object]) -> str:
    return _first(
        _clean(env.get(ENV_BACKEND_URL)),
        _clean(file_values.get("backendUrl")),
        DEFAULT_BACKEND_URL,
    )


def resolve_settings(
    *,
    env: Mapping[str, str],
    directory: Path | None = None,
    token_unprotector: Callable[[str], str] | None = None,
) -> WorkerSettings:
    """Merge environment variables and the configuration file into one answer."""
    path = config_file_path(directory)
    file_values = read_config_file(path)
    config_path = path if path.is_file() else None

    backend_url = _backend_url(env, file_values)
    worker_id = _first(
        _clean(env.get(ENV_WORKER_ID)),
        _clean(file_values.get("workerId")),
    )
    worker_token = _resolve_worker_token(
        env, file_values, token_unprotector=token_unprotector
    )
    hourly_price = _first(
        _env_integer(env, ENV_HOURLY_PRICE),
        file_values.get("hourlyPrice"),
        DEFAULT_HOURLY_PRICE,
    )
    gpu_index = _first(
        _env_integer(env, ENV_GPU_INDEX),
        file_values.get("gpuIndex"),
        DEFAULT_GPU_INDEX,
    )
    supported_workloads = _first(
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

    return WorkerSettings(
        backend_url=backend_url,
        worker_id=worker_id,
        hourly_price=hourly_price,
        gpu_index=gpu_index,
        supported_workloads=supported_workloads,
        worker_token=worker_token,
        config_path=config_path,
    )


def _missing_message(missing: list[str], path: Path, config_path: Path | None) -> str:
    if config_path is None and missing == ["workerId"]:
        return (
            "this machine is not enrolled yet, so it has no workerId. "
            f"{CONFIG_FILE_NAME} was not found at {path}. Copy "
            f"{EXAMPLE_FILE_NAME} to that name and fill it in; enrolling the "
            "machine and writing that file for you is not part of this build."
        )
    location = (
        f"Set them in {config_path}"
        if config_path is not None
        else f"Create {path} from {EXAMPLE_FILE_NAME}"
    )
    return (
        "the Worker configuration is incomplete. Missing required setting(s): "
        f"{', '.join(missing)}. {location}, or set the matching environment "
        "variables."
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
            # What went wrong carries the ciphertext and the Windows error.
            # Neither belongs on a supplier's screen, and every cause has the
            # same answer: these credentials are not readable on this account.
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
    items = tuple(
        item.strip() for item in value if isinstance(item, str) and item.strip()
    )
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
