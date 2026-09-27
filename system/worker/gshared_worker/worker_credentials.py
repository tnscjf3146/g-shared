"""Writing the Worker's identity to disk, and telling apart the ways it breaks.

Reading is :func:`gshared_worker.config.resolve_settings`; this is the other
half. The file and the key names are the ones that already exist --
``worker-config.json`` with ``workerId`` and ``workerTokenProtected`` -- so a
machine written by this code is read by code that predates it.

Three failures are deliberately not the same thing:

* **no file** -- this machine has never been registered. Ask a person to sign in.
* **a file that cannot be decrypted** -- somebody copied a credential here from
  another account or machine. Registering again would silently replace what
  they deliberately put there, so it is not done for them.
* **a file that is there and half-written** -- an interrupted write. Same
  treatment: say so, change nothing.

Confusing any of those with the first is how a Worker quietly re-enrols itself.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from .config import CONFIG_FILE_NAME, config_file_path
from .credentials import protect_current_user, unprotect_current_user
from .logging_setup import LOGGER_NAME

_debug = logging.getLogger(LOGGER_NAME)


class CredentialState(Enum):
    PRESENT = "PRESENT"
    #: Nothing on disk. A machine nobody has registered yet.
    ABSENT = "ABSENT"
    #: Something on disk that cannot be used: unreadable, half-written, or
    #: protected for a different Windows account.
    UNUSABLE = "UNUSABLE"


@dataclass(frozen=True)
class WorkerCredentials:
    worker_id: str
    worker_token: str


@dataclass(frozen=True)
class CredentialLoad:
    state: CredentialState
    credentials: WorkerCredentials | None = None
    detail: str = ""

    @property
    def is_present(self) -> bool:
        return self.state is CredentialState.PRESENT


def load_credentials(
    directory: Path | None = None,
    *,
    unprotector: Callable[[str], str] | None = None,
) -> CredentialLoad:
    """What this machine's stored identity is, or why it cannot be used."""
    path = config_file_path(directory)
    if not path.is_file():
        return CredentialLoad(CredentialState.ABSENT, detail=f"{path} does not exist")

    try:
        values = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        return CredentialLoad(
            CredentialState.UNUSABLE,
            detail=f"{CONFIG_FILE_NAME} could not be read: {error}",
        )
    if not isinstance(values, dict):
        return CredentialLoad(
            CredentialState.UNUSABLE,
            detail=f"{CONFIG_FILE_NAME} does not contain an object",
        )

    worker_id = values.get("workerId")
    sealed = values.get("workerTokenProtected")
    if not _filled(worker_id) and not _filled(sealed):
        # A settings file with no identity in it. Nobody has registered here.
        return CredentialLoad(
            CredentialState.ABSENT, detail=f"{CONFIG_FILE_NAME} holds no credentials")
    if not _filled(worker_id) or not _filled(sealed):
        # One without the other. An interrupted write, or a file edited by
        # hand. Half an identity is not an identity.
        return CredentialLoad(
            CredentialState.UNUSABLE,
            detail=(
                f"{CONFIG_FILE_NAME} has "
                f"{'workerId' if _filled(worker_id) else 'workerTokenProtected'} "
                "but not the other"
            ),
        )

    try:
        token = (unprotector or unprotect_current_user)(sealed)
    except Exception:
        # The failure carries the ciphertext and a Windows error code. Neither
        # belongs anywhere, and every cause has the same answer.
        return CredentialLoad(
            CredentialState.UNUSABLE,
            detail="CREDENTIAL_DECRYPTION_FAILED: the stored Worker token "
                   "cannot be read on this Windows account",
        )
    if not _filled(token):
        return CredentialLoad(
            CredentialState.UNUSABLE,
            detail="CREDENTIAL_DECRYPTION_FAILED: the stored Worker token is empty",
        )

    return CredentialLoad(
        CredentialState.PRESENT,
        WorkerCredentials(worker_id.strip(), token.strip()),
        detail=f"stored credentials for {worker_id.strip()}",
    )


def store_credentials(
    worker_id: str,
    worker_token: str,
    directory: Path | None = None,
    *,
    protector: Callable[[str], str] | None = None,
) -> Path:
    """Write the identity, with the token protected for this account only.

    Other settings already in the file are kept: a supplier may have set a
    price or a GPU index, and being registered is no reason to lose it.

    The write goes to a temporary file and is then moved into place, so an
    interrupted write cannot leave half an identity where a whole one was.
    """
    if not _filled(worker_id) or not _filled(worker_token):
        raise ValueError("both workerId and workerToken are required")

    path = config_file_path(directory)
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, dict):
                existing = loaded
        except (OSError, json.JSONDecodeError):
            # A file that cannot be read is replaced rather than merged;
            # keeping half of something unparseable helps nobody.
            existing = {}

    existing["workerId"] = worker_id.strip()
    existing["workerTokenProtected"] = (protector or protect_current_user)(worker_token)
    # A token left in the clear by an earlier version would otherwise survive
    # beside the protected one and be the thing that leaks.
    existing.pop("workerToken", None)

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".new")
    temporary.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    # The identity, never the token.
    _debug.debug("stored credentials for %s at %s", worker_id.strip(), path)
    return path


def clear_credentials(directory: Path | None = None) -> bool:
    """Forget the Worker identity, and nothing else. True when one was removed.

    For one situation only: the Backend said the stored token is no longer
    valid (``INVALID_WORKER_TOKEN``), so the identity is dead and keeping it
    would refuse every start. ``workerId`` and the sealed token go together --
    half an identity is never left behind -- through the same temporary-file
    move :func:`store_credentials` uses. Every other setting stays, and so does
    ``worker-wallet.json``, a separate file this never opens: the signing key
    is this machine's on-chain identity and outlives any Worker registration.

    A file that cannot be read is left exactly as it is. That is not a dead
    identity, it is an unknown one, and :func:`load_credentials` already
    reports it as UNUSABLE.
    """
    path = config_file_path(directory)
    if not path.is_file():
        return False
    try:
        values = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(values, dict):
        return False

    forgotten = values.get("workerId")
    removed = [key for key in ("workerId", "workerTokenProtected", "workerToken")
               if values.pop(key, None) is not None]
    if not removed:
        return False

    temporary = path.with_suffix(path.suffix + ".new")
    temporary.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    # The identity that was dropped, never the token.
    _debug.warning("cleared stored credentials for %s at %s",
                   forgotten.strip() if _filled(forgotten) else "(no id)", path)
    return True


def _filled(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())
