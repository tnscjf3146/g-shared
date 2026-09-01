"""Real Windows handle implementation used by the Setup instance guard."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path


_ERROR_ALREADY_EXISTS = 183
_ERROR_INSUFFICIENT_BUFFER = 122
_ERROR_SHARING_VIOLATION = 32
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_OPEN_ALWAYS = 4
_FILE_ATTRIBUTE_NORMAL = 0x80
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class _TokenUser(ctypes.Structure):
    _fields_ = [("User", _SidAndAttributes)]


class WindowsPlatform:
    """Uses operating-system owned handles so a process crash releases the lock."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("WindowsPlatform requires Windows")
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self._configure_api()

    def _configure_api(self) -> None:
        self._kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
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
        token = ctypes.c_void_p()
        if not self._advapi32.OpenProcessToken(
            self._kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)
        ):
            self._raise_last_error("OpenProcessToken")
        try:
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
            token_user = ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents
            value = wintypes.LPWSTR()
            if not self._advapi32.ConvertSidToStringSidW(token_user.User.Sid, ctypes.byref(value)):
                self._raise_last_error("ConvertSidToStringSidW")
            try:
                return value.value
            finally:
                self._kernel32.LocalFree(value)
        finally:
            self.close_handle(token)

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
