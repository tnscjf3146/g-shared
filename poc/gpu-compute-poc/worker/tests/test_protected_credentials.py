"""Tests for Current User DPAPI credential protection."""

from __future__ import annotations

import ctypes
import sys
import unittest

from gpu_worker.protected_credentials import (
    CRYPTPROTECT_UI_FORBIDDEN,
    _DataBlob,
    _WindowsDpapi,
    protect_current_user,
    unprotect_current_user,
)


class _RecordingDpapi:
    def __init__(self) -> None:
        self.protect_input: bytearray | None = None
        self.protect_output: bytearray | None = None
        self.unprotect_input: bytearray | None = None
        self.unprotect_output: bytearray | None = None
        self.flags: list[int] = []

    def protect(self, plaintext: bytearray, flags: int) -> bytearray:
        self.protect_input = plaintext
        self.flags.append(flags)
        self.protect_output = bytearray(reversed(plaintext))
        return self.protect_output

    def unprotect(self, protected: bytearray, flags: int) -> bytearray:
        self.unprotect_input = protected
        self.flags.append(flags)
        self.unprotect_output = bytearray(reversed(protected))
        return self.unprotect_output


class ProtectedCredentialsTests(unittest.TestCase):
    def test_injected_dpapi_round_trip_forbids_ui_and_zeroes_mutable_buffers(self) -> None:
        api = _RecordingDpapi()

        encoded = protect_current_user("credential-value", api=api)
        restored = unprotect_current_user(encoded, api=api)

        self.assertEqual(restored, "credential-value")
        self.assertEqual(api.flags, [CRYPTPROTECT_UI_FORBIDDEN] * 2)
        for buffer in (
            api.protect_input,
            api.protect_output,
            api.unprotect_input,
            api.unprotect_output,
        ):
            self.assertIsNotNone(buffer)
            self.assertTrue(all(value == 0 for value in buffer))

    def test_unprotect_rejects_noncanonical_or_invalid_base64_before_dpapi(self) -> None:
        for encoded in ("not-base64", "YQ", "YR==", "YQ==\n"):
            with self.subTest(encoded=repr(encoded)):
                api = _RecordingDpapi()
                with self.assertRaises(ValueError):
                    unprotect_current_user(encoded, api=api)
                self.assertEqual(api.flags, [])

    @unittest.skipUnless(sys.platform == "win32", "Windows ctypes behavior is required")
    def test_native_failure_still_zeroes_and_frees_returned_output(self) -> None:
        class FailingNativeCall:
            def __init__(self) -> None:
                self.output = (ctypes.c_ubyte * 4)(1, 2, 3, 4)

            def __call__(self, *_arguments: object) -> int:
                output_pointer = ctypes.cast(
                    _arguments[-1], ctypes.POINTER(_DataBlob)
                )
                output_pointer.contents.cbData = len(self.output)
                output_pointer.contents.pbData = ctypes.cast(
                    self.output, ctypes.POINTER(ctypes.c_ubyte)
                )
                ctypes.set_last_error(5)
                return 0

        class RecordingKernel:
            def __init__(self) -> None:
                self.freed_address: int | None = None

            def LocalFree(self, pointer: object) -> None:
                self.freed_address = ctypes.cast(pointer, ctypes.c_void_p).value

        function = FailingNativeCall()
        kernel = RecordingKernel()
        api = _WindowsDpapi.__new__(_WindowsDpapi)
        api._kernel32 = kernel

        with self.assertRaises(OSError) as caught:
            api._call(function, bytearray(b"input"), CRYPTPROTECT_UI_FORBIDDEN)

        self.assertEqual(caught.exception.winerror, 5)
        self.assertEqual(list(function.output), [0, 0, 0, 0])
        self.assertEqual(
            kernel.freed_address,
            ctypes.addressof(function.output),
        )

    @unittest.skipUnless(sys.platform == "win32", "Windows DPAPI is required")
    def test_real_current_user_dpapi_round_trip(self) -> None:
        encoded = protect_current_user("round-trip-value")

        self.assertEqual(unprotect_current_user(encoded), "round-trip-value")


if __name__ == "__main__":
    unittest.main()
