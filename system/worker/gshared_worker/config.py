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
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .credentials import unprotect_current_user
from .errors import WorkerConfigurationError

CONFIG_FILE_NAME = "worker-config.json"
WALLET_FILE_NAME = "worker-wallet.json"
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
#: Written as the service's own address. Both addresses the Worker uses are
#: derived from it and cannot drift apart: signing in goes to its HTTPS origin
#: (``_http_origin_of`` in :mod:`gshared_worker.auth_check`), and the socket to
#: ``wss://`` on the same host with the path the Backend registered
#: (:func:`gshared_worker.worker_socket.websocket_url`).
DEFAULT_BACKEND_URL = "https://j15e204.p.ssafy.io"

#: The node the Worker reads the escrow from and sends its transactions to,
#: when nobody has said otherwise. The SSAFY chain's public RPC.
DEFAULT_ESCROW_RPC_URL = "https://rpc.ssafy-blockchain.com"

#: The chain every escrow acceptance must be on. The SSAFY chain; the same
#: number as ``escrow.SSAFY_CHAIN_ID``.
DEFAULT_CHAIN_ID = 31221

#: The GPUJobEscrow the Blockchain team deployed on the SSAFY chain, as recorded
#: in ``blockchain/deployments/ssafy-31221/GPUJobEscrow.json``. Not typed from
#: memory: a test compares it with that record, so a redeployment that is not
#: carried here fails the build instead of shipping a Worker that signs against
#: the old contract. A local chain overrides it like the Backend address.
DEFAULT_ESCROW_ADDRESS = "0x66178DF08F5938d2ABC17496b6A98F7d776D3c56"

#: An Ethereum address: ``0x`` and forty hex digits.
_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")

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
ENV_ESCROW_RPC_URL = "GSHARED_WORKER_ESCROW_RPC_URL"
ENV_ESCROW_ADDRESS = "GSHARED_WORKER_ESCROW_ADDRESS"
ENV_CHAIN_ID = "GSHARED_WORKER_CHAIN_ID"

_STRING_KEYS = ("backendUrl", "workerId", "workerToken", "escrowRpcUrl", "escrowAddress")
_INTEGER_KEYS = ("hourlyPrice", "gpuIndex", "chainId")
_LIST_KEYS = ("supportedWorkloads",)
_KNOWN_KEYS = _STRING_KEYS + ("workerTokenProtected",) + _INTEGER_KEYS + _LIST_KEYS


@dataclass(frozen=True)
class WorkerSettings:
    backend_url: str
    #: None on a machine that has never been registered. It is issued by
    #: registration, so it cannot be asked for before it: that machine goes on
    #: to the sign-in window rather than stopping here.
    worker_id: str | None
    hourly_price: int
    gpu_index: int
    supported_workloads: tuple[str, ...]
    worker_token: str | None
    config_path: Path | None
    #: Where on-chain work is checked and signed. Defaulted to the SSAFY chain
    #: and its deployed escrow. ``escrow_address=None`` is a Worker with no
    #: contract, which refuses every assignment.
    escrow_rpc_url: str = DEFAULT_ESCROW_RPC_URL
    escrow_address: str | None = DEFAULT_ESCROW_ADDRESS
    chain_id: int = DEFAULT_CHAIN_ID


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


#: The per-user folder a packaged Worker keeps its files in, under
#: %LOCALAPPDATA%. The same root the instance lock already uses.
USER_DATA_DIRECTORY_NAME = "G-shared"


def data_directory(env: Mapping[str, str] | None = None) -> Path:
    """Where this Worker reads and writes its own files.

    Settings, the sealed token, the sealed signing key and the debug log.

    Run from source, that is :func:`worker_directory`, exactly as before.

    Packaged, the executable may sit somewhere its user cannot write --
    ``C:\\Program Files`` is the ordinary case -- and a Worker that tries to
    open its log there dies before it can say why. So a packaged Worker keeps
    its files in ``%LOCALAPPDATA%\\G-shared``, one folder per Windows account,
    which is also the account DPAPI seals them to.

    The one exception is a **portable** copy: if ``worker-config.json`` or
    ``worker-wallet.json`` is already beside the executable, that folder is
    used. The wallet alone is enough to pin the location: deleting or resetting
    Worker credentials must not move the Operator identity to LocalAppData and
    quietly create another one.

    If both locations already contain wallets, their public addresses must
    agree before portable precedence is used. The private keys are not opened
    here. A disagreement, or address metadata that cannot be compared safely,
    is an explicit storage conflict rather than a guess between identities.
    """
    program = worker_directory()
    if not getattr(sys, "frozen", False):
        return program
    root = (os.environ if env is None else env).get("LOCALAPPDATA")
    if not root:
        # Nowhere per-user to go. Beside the program is still better than
        # refusing to start; if it is not writable the log says so.
        return program
    local = Path(root) / USER_DATA_DIRECTORY_NAME
    portable_wallet = program / WALLET_FILE_NAME
    local_wallet = local / WALLET_FILE_NAME
    if (portable_wallet.is_file() and local_wallet.is_file()
            and program != local):
        _require_one_operator_identity(portable_wallet, local_wallet)
    if ((program / CONFIG_FILE_NAME).is_file() or portable_wallet.is_file()):
        return program
    return local


def _require_one_operator_identity(portable: Path, local: Path) -> None:
    """Refuse two storage roots unless they name the same public identity."""
    portable_address = _stored_operator_address(portable)
    local_address = _stored_operator_address(local)
    if portable_address is None or local_address is None:
        raise WorkerConfigurationError(
            "STORAGE_IDENTITY_CONFLICT: portable and LocalAppData both contain "
            "Worker wallets, but their Operator addresses cannot be compared "
            "safely. Neither wallet was selected or changed."
        )
    if portable_address.lower() != local_address.lower():
        raise WorkerConfigurationError(
            "STORAGE_IDENTITY_CONFLICT: portable and LocalAppData contain "
            f"different Worker Operators ({_short_address(portable_address)} / "
            f"{_short_address(local_address)}). Neither wallet was selected "
            "or changed."
        )


def _stored_operator_address(path: Path) -> str | None:
    """Read only the public address used to disambiguate two storage roots."""
    try:
        values = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(values, dict):
        return None
    address = values.get("address")
    if not isinstance(address, str) or not _ADDRESS.match(address):
        return None
    return address


def _short_address(address: str) -> str:
    return f"{address[:6]}...{address[-4:]}"


def config_file_path(directory: Path | None = None) -> Path:
    """Where the settings and the stored credentials live, in one file."""
    return (directory or data_directory()) / CONFIG_FILE_NAME


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

    escrow_rpc_url = _first(
        _clean(env.get(ENV_ESCROW_RPC_URL)),
        _clean(file_values.get("escrowRpcUrl")),
        DEFAULT_ESCROW_RPC_URL,
    )
    escrow_address = _first(
        _clean(env.get(ENV_ESCROW_ADDRESS)),
        _clean(file_values.get("escrowAddress")),
        DEFAULT_ESCROW_ADDRESS,
    )
    if escrow_address is not None and not _ADDRESS.match(escrow_address):
        # A typo here is a contract nobody deployed. Refused now, rather than
        # found when the first Job's deposit is waiting on it.
        raise WorkerConfigurationError(
            f"escrowAddress must be 0x and forty hex digits, found {escrow_address!r}")
    chain_id = _first(
        _env_integer(env, ENV_CHAIN_ID),
        file_values.get("chainId"),
        DEFAULT_CHAIN_ID,
    )

    # workerId is not on this list. It is what registration hands back, so a
    # first run has none, and refusing to start without one was a machine that
    # could never be registered from the executable at all.
    missing = [
        key
        for key, value in (
            ("backendUrl", backend_url),
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
        escrow_rpc_url=escrow_rpc_url,
        escrow_address=escrow_address,
        chain_id=chain_id,
    )


def _missing_message(missing: list[str], path: Path, config_path: Path | None) -> str:
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
