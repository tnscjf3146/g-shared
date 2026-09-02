"""Tests for the two-layer Setup single-instance lock."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from gpu_worker_setup.platform_api import SetupInstanceLock
from gpu_worker_setup.windows_platform import WindowsPlatform


class FakeWindowsApi:
    def __init__(
        self,
        *,
        mutex_already_exists: bool = False,
        file_locked: bool = False,
        open_failure: BaseException | None = None,
        close_failure_on: str | None = None,
    ) -> None:
        self.mutex_already_exists = mutex_already_exists
        self.file_locked = file_locked
        self.open_failure = open_failure
        self.close_failure_on = close_failure_on
        self.closed: list[object] = []
        self.mutex_names: list[str] = []
        self.lock_paths: list[Path] = []
        self._active_mutexes: set[str] = set()

    def current_user_sid(self) -> str:
        return "S-1-5-21-123456789-123456789-123456789-1001"

    def create_named_mutex(self, name: str) -> tuple[object, bool]:
        self.mutex_names.append(name)
        already_exists = self.mutex_already_exists or name in self._active_mutexes
        if not already_exists:
            self._active_mutexes.add(name)
        return ("mutex", name), already_exists

    def open_share_none_lock(self, path: Path) -> object | None:
        self.lock_paths.append(path)
        if self.open_failure is not None:
            raise self.open_failure
        if self.file_locked:
            return None
        return ("file", str(path))

    def close_handle(self, handle: object) -> None:
        self.closed.append(handle)
        if isinstance(handle, tuple) and handle[0] == "mutex":
            self._active_mutexes.discard(handle[1])
        if isinstance(handle, tuple) and handle[0] == self.close_failure_on:
            raise OSError(f"{handle[0]} close failed")


class SetupInstanceLockTests(unittest.TestCase):
    def test_second_setup_lock_fails_without_touching_state(self) -> None:
        fake_windows = FakeWindowsApi()
        path = Path("C:/state/setup.lock")
        first = SetupInstanceLock(fake_windows, path)
        second = SetupInstanceLock(fake_windows, path)

        self.assertTrue(first.acquire())
        lock_attempts_before_second = len(fake_windows.lock_paths)
        self.assertFalse(second.acquire())
        self.assertEqual(second.issue_code, "SETUP_ALREADY_RUNNING")
        self.assertEqual(len(fake_windows.lock_paths), lock_attempts_before_second)
        self.assertEqual(len(fake_windows.closed), 1)
        first.release()

    def test_file_lock_collision_releases_the_mutex(self) -> None:
        fake_windows = FakeWindowsApi(file_locked=True)
        lock = SetupInstanceLock(fake_windows, Path("C:/state/setup.lock"))

        self.assertFalse(lock.acquire())
        self.assertEqual(lock.issue_code, "SETUP_ALREADY_RUNNING")
        self.assertEqual(len(fake_windows.lock_paths), 1)
        self.assertEqual(len(fake_windows.closed), 1)

    def test_mutex_name_contains_only_an_opaque_sid_hash(self) -> None:
        fake_windows = FakeWindowsApi()
        lock = SetupInstanceLock(fake_windows, Path("C:/state/setup.lock"))

        self.assertTrue(lock.acquire())

        self.assertEqual(len(fake_windows.mutex_names), 1)
        self.assertRegex(fake_windows.mutex_names[0], r"^Local\\GPUComputeSetup-[0-9a-f]{64}$")
        self.assertNotIn(fake_windows.current_user_sid(), fake_windows.mutex_names[0])
        lock.release()
        self.assertEqual(len(fake_windows.closed), 2)

    def test_context_manager_releases_both_handles_after_use(self) -> None:
        fake_windows = FakeWindowsApi()

        with SetupInstanceLock(fake_windows, Path("C:/state/setup.lock")) as lock:
            self.assertIsNone(lock.issue_code)

        self.assertEqual(len(fake_windows.closed), 2)

    def test_release_attempts_mutex_close_when_file_close_fails(self) -> None:
        fake_windows = FakeWindowsApi(close_failure_on="file")
        lock = SetupInstanceLock(fake_windows, Path("C:/state/setup.lock"))
        self.assertTrue(lock.acquire())

        with self.assertRaisesRegex(OSError, "file close failed"):
            lock.release()

        self.assertEqual([handle[0] for handle in fake_windows.closed], ["file", "mutex"])

    def test_context_manager_preserves_the_body_error_when_cleanup_fails(self) -> None:
        fake_windows = FakeWindowsApi(close_failure_on="file")

        with self.assertRaisesRegex(ValueError, "body failed"):
            with SetupInstanceLock(fake_windows, Path("C:/state/setup.lock")):
                raise ValueError("body failed")

        self.assertEqual([handle[0] for handle in fake_windows.closed], ["file", "mutex"])

    def test_acquire_preserves_open_failure_when_mutex_cleanup_fails(self) -> None:
        fake_windows = FakeWindowsApi(
            open_failure=OSError("open failed"), close_failure_on="mutex"
        )
        lock = SetupInstanceLock(fake_windows, Path("C:/state/setup.lock"))

        with self.assertRaisesRegex(OSError, "open failed"):
            lock.acquire()

        self.assertEqual([handle[0] for handle in fake_windows.closed], ["mutex"])

    @unittest.skipUnless(__import__("os").name == "nt", "requires Windows handle APIs")
    def test_real_same_process_windows_lock_rejects_the_second_acquisition(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "setup.lock"
            platform = WindowsPlatform()
            first = SetupInstanceLock(platform, path)
            second = SetupInstanceLock(platform, path)

            self.assertTrue(first.acquire())
            try:
                self.assertFalse(second.acquire())
                self.assertEqual(second.issue_code, "SETUP_ALREADY_RUNNING")
            finally:
                second.release()
                first.release()


if __name__ == "__main__":
    unittest.main()
