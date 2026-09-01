"""Current-user single-instance guard acquired before any Backend work."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from hashlib import sha256
import os
from pathlib import Path
from typing import Protocol

from .errors import WorkerAlreadyRunningError


ISSUE_CODE = "WORKER_ALREADY_RUNNING"

_ERROR_ALREADY_EXISTS = 183
_ERROR_INSUFFICIENT_BUFFER = 122
_ERROR_SHARING_VIOLATION = 32
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_OPEN_ALWAYS = 4
_FILE_ATTRIBUTE_NORMAL = 0x80
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1
_MUTEX_NAME_DOMAIN = "gpu-compute-worker-instance-mutex"


class WindowsMutexApi(Protocol):
    """The minimum Windows handle surface the guard needs."""

    def current_user_sid(self) -> str: ...

    def create_named_mutex(self, name: str) -> tuple[object, bool]: ...

    def open_share_none_lock(self, path: Path) -> object | None: ...

    def close_handle(self, handle: object) -> None: ...


def worker_instance_mutex_name(sid: str, worker_id: str, gpu_index: int) -> str:
    """Derive the session-local mutex name for one Worker identity.

    The identity is exactly the current user, the Worker ID, and the GPU index, so
    neither the raw Worker ID nor the user's SID appears in a name any process can
    enumerate.
    """
    return f"Local\\GPUComputeWorker-{worker_instance_digest(sid, worker_id, gpu_index)}"


def worker_instance_digest(sid: str, worker_id: str, gpu_index: int) -> str:
    """Hash the identity with an unambiguous separator and a domain label.

    The separator stops one identity from being forged by shifting a boundary
    between the three parts, and the domain label keeps this digest distinct from
    the one naming the world-enumerable control pipe.
    """
    validate_worker_identity(worker_id, gpu_index)
    if not isinstance(sid, str) or not sid.strip():
        raise ValueError("sid must be a non-empty string")
    return sha256(
        "\x1f".join((_MUTEX_NAME_DOMAIN, sid, worker_id, str(gpu_index))).encode("utf-8")
    ).hexdigest()


def default_lock_directory() -> Path:
    """The per-user state directory the Setup install layout owns."""
    root = os.environ.get("LOCALAPPDATA")
    if not root:
        raise ValueError("LOCALAPPDATA is not set for this user")
    return Path(root) / "GPUCompute" / "state"


def validate_worker_identity(worker_id: object, gpu_index: object) -> None:
    if not isinstance(worker_id, str) or not worker_id.strip():
        raise ValueError("worker_id must be a non-empty string")
    if isinstance(gpu_index, bool) or not isinstance(gpu_index, int) or gpu_index < 0:
        raise ValueError("gpu_index must be a non-negative integer")


class WindowsMutexPlatform:
    """Operating-system owned handles, so a crashed Worker releases its identity."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("the Worker instance guard requires Windows")
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self._configure_api()

    def _configure_api(self) -> None:
        self._kernel32.CreateMutexW.argtypes = [
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        self._kernel32.CreateMutexW.restype = ctypes.c_void_p
        self._kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
        ]
        self._kernel32.CreateFileW.restype = ctypes.c_void_p
        self._kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self._kernel32.CloseHandle.restype = wintypes.BOOL
        self._kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        self._kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self._kernel32.LocalFree.restype = ctypes.c_void_p
        self._advapi32.OpenProcessToken.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self._advapi32.OpenProcessToken.restype = wintypes.BOOL
        self._advapi32.GetTokenInformation.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._advapi32.GetTokenInformation.restype = wintypes.BOOL
        self._advapi32.ConvertSidToStringSidW.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.LPWSTR),
        ]
        self._advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL

    def current_user_sid(self) -> str:
        handle = ctypes.c_void_p()
        if not self._advapi32.OpenProcessToken(
            self._kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(handle)
        ):
            self._raise_last_error("OpenProcessToken")
        try:
            return self.sid_from_token(handle)
        finally:
            try:
                self.close_handle(handle)
            except OSError:
                pass

    def sid_from_token(self, token: object) -> str:
        required = wintypes.DWORD()
        ctypes.set_last_error(0)
        self._advapi32.GetTokenInformation(
            token, _TOKEN_USER, None, 0, ctypes.byref(required)
        )
        if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER:
            self._raise_last_error("GetTokenInformation size")
        buffer = ctypes.create_string_buffer(required.value)
        if not self._advapi32.GetTokenInformation(
            token, _TOKEN_USER, buffer, required, ctypes.byref(required)
        ):
            self._raise_last_error("GetTokenInformation")
        sid = ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents.User.Sid
        value = wintypes.LPWSTR()
        if not self._advapi32.ConvertSidToStringSidW(sid, ctypes.byref(value)):
            self._raise_last_error("ConvertSidToStringSidW")
        try:
            return value.value
        finally:
            self._kernel32.LocalFree(value)

    def create_named_mutex(self, name: str) -> tuple[object, bool]:
        ctypes.set_last_error(0)
        handle = self._kernel32.CreateMutexW(None, False, name)
        if not handle:
            self._raise_last_error("CreateMutexW")
        return handle, ctypes.get_last_error() == _ERROR_ALREADY_EXISTS

    def open_share_none_lock(self, path: Path) -> object | None:
        path.parent.mkdir(parents=True, exist_ok=True)
        ctypes.set_last_error(0)
        handle = self._kernel32.CreateFileW(
            str(path),
            _GENERIC_READ | _GENERIC_WRITE,
            0,
            None,
            _OPEN_ALWAYS,
            _FILE_ATTRIBUTE_NORMAL,
            None,
        )
        if handle == _INVALID_HANDLE_VALUE:
            if ctypes.get_last_error() == _ERROR_SHARING_VIOLATION:
                return None
            self._raise_last_error("CreateFileW")
        return handle

    def close_handle(self, handle: object) -> None:
        if not self._kernel32.CloseHandle(handle):
            self._raise_last_error("CloseHandle")

    @staticmethod
    def _raise_last_error(operation: str) -> None:
        error = ctypes.get_last_error()
        raise OSError(error, f"{operation} failed")


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class _TokenUser(ctypes.Structure):
    _fields_ = [("User", _SidAndAttributes)]


class WorkerInstanceGuard:
    """Holds one Worker identity for this user until the process releases it.

    The session-local mutex settles the common same-session race quickly. The
    share-none file lock extends the same identity across every Windows session
    this user has, so a console login and an RDP login cannot both register the
    same Worker ID on the same GPU. Both are operating-system handles, so a
    crashed Worker releases its identity without any cleanup code of ours.
    """

    ISSUE_CODE = ISSUE_CODE

    def __init__(
        self,
        worker_id: str,
        gpu_index: int,
        *,
        api: WindowsMutexApi | None = None,
        lock_directory: Path | None = None,
    ) -> None:
        validate_worker_identity(worker_id, gpu_index)
        self._worker_id = worker_id
        self._gpu_index = gpu_index
        self._api = api or WindowsMutexPlatform()
        self._lock_directory = lock_directory
        self._handle: object | None = None
        self._lock_handle: object | None = None
        self.issue_code: str | None = None

    @property
    def acquired(self) -> bool:
        return self._handle is not None and self._lock_handle is not None

    def acquire(self) -> bool:
        """Take the identity, or report that another Worker already holds it."""
        if self.acquired:
            return True

        digest = worker_instance_digest(
            self._api.current_user_sid(), self._worker_id, self._gpu_index
        )
        handle, already_exists = self._api.create_named_mutex(
            f"Local\\GPUComputeWorker-{digest}"
        )
        if already_exists:
            # The duplicate handle refers to the same named object; closing it does
            # not disturb the owner, and keeping it would leak.
            self._api.close_handle(handle)
            self.issue_code = ISSUE_CODE
            return False

        try:
            lock_handle = self._api.open_share_none_lock(self._lock_path(digest))
        except BaseException:
            self._close_quietly(handle)
            raise
        if lock_handle is None:
            self._close_quietly(handle)
            self.issue_code = ISSUE_CODE
            return False

        self._handle = handle
        self._lock_handle = lock_handle
        self.issue_code = None
        return True

    def release(self) -> None:
        for attribute in ("_lock_handle", "_handle"):
            handle = getattr(self, attribute)
            setattr(self, attribute, None)
            if handle is not None:
                self._close_quietly(handle)

    def _lock_path(self, digest: str) -> Path:
        directory = self._lock_directory or default_lock_directory()
        return Path(directory) / f"worker-{digest}.lock"

    def _close_quietly(self, handle: object) -> None:
        try:
            self._api.close_handle(handle)
        except OSError:
            pass

    def __enter__(self) -> "WorkerInstanceGuard":
        if not self.acquire():
            raise WorkerAlreadyRunningError(
                f"{ISSUE_CODE}: another Worker already runs this GPU for this user"
            )
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        self.release()
        return False
