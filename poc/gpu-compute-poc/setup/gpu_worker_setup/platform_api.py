"""Small platform boundary for Setup's Windows-only single-instance guard."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Protocol


class WindowsPlatformApi(Protocol):
    """The minimum Windows handle operations used by :class:`SetupInstanceLock`."""

    def current_user_sid(self) -> str: ...

    def create_named_mutex(self, name: str) -> tuple[object, bool]: ...

    def open_share_none_lock(self, path: Path) -> object | None: ...

    def close_handle(self, handle: object) -> None: ...


class SetupInstanceLock:
    """Combines a SID-scoped mutex with a cross-session share-none file handle."""

    ISSUE_CODE = "SETUP_ALREADY_RUNNING"

    def __init__(self, platform: WindowsPlatformApi, lock_path: str | Path) -> None:
        self._platform = platform
        self._lock_path = Path(lock_path)
        self._mutex_handle: object | None = None
        self._file_handle: object | None = None
        self.issue_code: str | None = None

    def acquire(self) -> bool:
        if self._mutex_handle is not None and self._file_handle is not None:
            return True

        sid_hash = sha256(self._platform.current_user_sid().encode("utf-8")).hexdigest()
        mutex_handle, already_exists = self._platform.create_named_mutex(
            f"Local\\GPUComputeSetup-{sid_hash}"
        )
        if already_exists:
            self._platform.close_handle(mutex_handle)
            self.issue_code = self.ISSUE_CODE
            return False

        try:
            file_handle = self._platform.open_share_none_lock(self._lock_path)
        except BaseException:
            try:
                self._platform.close_handle(mutex_handle)
            except BaseException:
                pass
            raise
        if file_handle is None:
            self._platform.close_handle(mutex_handle)
            self.issue_code = self.ISSUE_CODE
            return False

        self._mutex_handle = mutex_handle
        self._file_handle = file_handle
        self.issue_code = None
        return True

    def release(self) -> None:
        first_error: BaseException | None = None
        if self._file_handle is not None:
            file_handle = self._file_handle
            self._file_handle = None
            try:
                self._platform.close_handle(file_handle)
            except BaseException as error:
                first_error = error
        if self._mutex_handle is not None:
            mutex_handle = self._mutex_handle
            self._mutex_handle = None
            try:
                self._platform.close_handle(mutex_handle)
            except BaseException as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error

    def __enter__(self) -> "SetupInstanceLock":
        if not self.acquire():
            raise RuntimeError(self.ISSUE_CODE)
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> bool:
        try:
            self.release()
        except BaseException:
            if exc_type is not None:
                return False
            raise
        return False
