"""Reading the Worker token back off the disk, on this Windows account only.

The token is stored by Windows DPAPI in Current User scope, so the file that
holds it is useless on any other account or any other machine. Only the reading
direction is here: writing one is what enrolment does, and enrolment is not part
of this build.
"""

from __future__ import annotations

import base64
import binascii
import ctypes
import sys
from typing import Protocol


CRYPTPROTECT_UI_FORBIDDEN = 0x00000001


class Dpapi(Protocol):
    """Narrow DPAPI boundary, so tests need no real protected value."""

    def unprotect(self, protected: bytearray, flags: int) -> bytearray: ...


def unprotect_current_user(encoded: str, *, api: Dpapi | None = None) -> str:
    """Strictly decode and unprotect a Current User DPAPI value."""
    protected = _strict_base64_decode(encoded)
    plaintext: bytearray | None = None
    try:
        plaintext = (api or _WindowsDpapi()).unprotect(
            protected, CRYPTPROTECT_UI_FORBIDDEN
        )
        try:
            return plaintext.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ValueError("DPAPI plaintext is not valid UTF-8") from error
    finally:
        _zero(protected)
        if plaintext is not None:
            _zero(plaintext)


def _strict_base64_decode(encoded: str) -> bytearray:
    if not isinstance(encoded, str) or not encoded:
        raise ValueError("protected credential must be non-empty base64")
    try:
        ascii_value = encoded.encode("ascii", errors="strict")
        decoded = base64.b64decode(ascii_value, validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as error:
        raise ValueError("protected credential must be valid base64") from error
    if not decoded or base64.b64encode(decoded) != ascii_value:
        raise ValueError("protected credential must be canonical base64")
    return bytearray(decoded)


def _zero(buffer: bytearray) -> None:
    for index in range(len(buffer)):
        buffer[index] = 0


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_ulong),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class _WindowsDpapi:
    """Thin owner of CryptUnprotectData output allocation and cleanup."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("Windows DPAPI is unavailable on this platform")
        self._crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_signatures()

    def _configure_signatures(self) -> None:
        blob_pointer = ctypes.POINTER(_DataBlob)
        self._crypt32.CryptUnprotectData.argtypes = [
            blob_pointer,
            ctypes.c_void_p,
            blob_pointer,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_ulong,
            blob_pointer,
        ]
        self._crypt32.CryptUnprotectData.restype = ctypes.c_int
        self._kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self._kernel32.LocalFree.restype = ctypes.c_void_p

    def unprotect(self, protected: bytearray, flags: int) -> bytearray:
        if not protected:
            raise ValueError("DPAPI input must not be empty")
        source_array = (ctypes.c_ubyte * len(protected)).from_buffer(protected)
        input_blob = _DataBlob(
            len(protected), ctypes.cast(source_array, ctypes.POINTER(ctypes.c_ubyte))
        )
        output_blob = _DataBlob()
        succeeded = self._crypt32.CryptUnprotectData(
            ctypes.byref(input_blob),
            None,
            None,
            None,
            None,
            flags,
            ctypes.byref(output_blob),
        )
        last_error = ctypes.get_last_error()
        try:
            if not succeeded:
                raise ctypes.WinError(last_error)
            return bytearray(ctypes.string_at(output_blob.pbData, output_blob.cbData))
        finally:
            if output_blob.pbData:
                ctypes.memset(output_blob.pbData, 0, output_blob.cbData)
                self._kernel32.LocalFree(output_blob.pbData)
