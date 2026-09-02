"""Tests for the current-user single-instance guard that gates REGISTER."""

from __future__ import annotations

import inspect
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from uuid import uuid4

from gpu_worker import instance_guard
from gpu_worker.instance_guard import (
    WorkerInstanceGuard,
    worker_instance_mutex_name,
)
from tempfile import TemporaryDirectory


OTHER_SID = "S-1-5-21-0000000000-0000000000-0000000000-2002"
OWNER_SID = "S-1-5-21-1111111111-2222222222-3333333333-1001"
LOCKS = "C:\\fake-state"


class _FakeWindowsApi:
    """Models both handles: each name is taken until its owner closes it.

    ``session`` distinguishes the session-local mutex namespace from the file
    system, which every session of a user shares.
    """

    def __init__(
        self,
        sid: str = OWNER_SID,
        *,
        session: int = 1,
        world: dict[str, dict] | None = None,
    ) -> None:
        self._sid = sid
        self._session = session
        shared = world if world is not None else {"mutexes": {}, "files": {}}
        self._mutexes: dict[tuple[int, str], object] = shared["mutexes"]
        self._files: dict[str, object] = shared["files"]
        self.world = shared
        self._names: dict[int, tuple[str, object]] = {}
        self._next_handle = 1
        self.opened: list[object] = []
        self.closed: list[object] = []

    def current_user_sid(self) -> str:
        return self._sid

    def create_named_mutex(self, name: str) -> tuple[object, bool]:
        handle = self._new_handle()
        key = (self._session, name)
        if key in self._mutexes:
            return handle, True
        self._mutexes[key] = handle
        self._names[handle] = ("mutexes", key)
        return handle, False

    def open_share_none_lock(self, path) -> object | None:
        key = str(path)
        if key in self._files:
            return None
        handle = self._new_handle()
        self._files[key] = handle
        self._names[handle] = ("files", key)
        return handle

    def close_handle(self, handle: object) -> None:
        self.closed.append(handle)
        entry = self._names.pop(handle, None)
        if entry is not None:
            store, key = entry
            self.world[store].pop(key, None)

    def _new_handle(self) -> int:
        handle = self._next_handle
        self._next_handle += 1
        self.opened.append(handle)
        return handle

    @property
    def leaked(self) -> list[object]:
        return [handle for handle in self.opened if handle not in self.closed]


class WorkerInstanceIdentityTests(unittest.TestCase):
    def test_the_same_user_worker_and_gpu_produce_one_identity(self) -> None:
        first = worker_instance_mutex_name(OWNER_SID, "worker-001", 0)
        second = worker_instance_mutex_name(OWNER_SID, "worker-001", 0)

        self.assertEqual(first, second)

    def test_user_worker_and_gpu_each_change_the_identity(self) -> None:
        base = worker_instance_mutex_name(OWNER_SID, "worker-001", 0)
        variants = (
            worker_instance_mutex_name(OTHER_SID, "worker-001", 0),
            worker_instance_mutex_name(OWNER_SID, "worker-002", 0),
            worker_instance_mutex_name(OWNER_SID, "worker-001", 1),
        )

        self.assertEqual(len(set(variants) | {base}), 4)

    def test_the_identity_cannot_be_forged_by_shifting_a_boundary(self) -> None:
        # Concatenating the three parts without a separator would make each of
        # these pairs collide, letting one identity impersonate another.
        self.assertNotEqual(
            worker_instance_mutex_name(OWNER_SID, "worker-1", 10),
            worker_instance_mutex_name(OWNER_SID, "worker-11", 0),
        )
        self.assertNotEqual(
            worker_instance_mutex_name("S-1-5-21-1", "1-worker", 0),
            worker_instance_mutex_name("S-1-5-21-11", "-worker", 0),
        )

    def test_the_name_is_session_local_and_hides_the_raw_worker_id(self) -> None:
        name = worker_instance_mutex_name(OWNER_SID, "worker-001", 0)

        self.assertTrue(name.startswith("Local\\GPUComputeWorker-"))
        self.assertNotIn("worker-001", name)
        self.assertNotIn(OWNER_SID, name)

    def test_no_token_can_reach_the_instance_identity(self) -> None:
        parameters = set(
            inspect.signature(WorkerInstanceGuard.__init__).parameters
        ) | set(inspect.signature(worker_instance_mutex_name).parameters)

        self.assertEqual(
            parameters,
            {"self", "worker_id", "gpu_index", "api", "lock_directory", "sid"},
        )
        source = Path(instance_guard.__file__).read_text(encoding="utf-8").lower()
        for secret in ("worker_token", "workertoken", "bearer", "authorization",
                       "credential", "secret", "password"):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, source)


class WorkerInstanceGuardTests(unittest.TestCase):
    def test_second_worker_with_same_user_id_and_gpu_is_rejected(self) -> None:
        api = _FakeWindowsApi()

        with WorkerInstanceGuard("worker-001", 0, api=api, lock_directory=LOCKS) as first:
            self.assertTrue(first.acquired)
            second = WorkerInstanceGuard("worker-001", 0, api=api, lock_directory=LOCKS)

            self.assertFalse(second.acquire())
            self.assertFalse(second.acquired)
            self.assertEqual(second.issue_code, "WORKER_ALREADY_RUNNING")

    def test_a_different_worker_id_is_allowed(self) -> None:
        api = _FakeWindowsApi()

        with WorkerInstanceGuard("worker-001", 0, api=api, lock_directory=LOCKS):
            self.assertTrue(WorkerInstanceGuard("worker-002", 0, api=api, lock_directory=LOCKS).acquire())

    def test_a_different_gpu_index_is_allowed(self) -> None:
        api = _FakeWindowsApi()

        with WorkerInstanceGuard("worker-001", 0, api=api, lock_directory=LOCKS):
            self.assertTrue(WorkerInstanceGuard("worker-001", 1, api=api, lock_directory=LOCKS).acquire())

    def test_a_different_user_is_allowed(self) -> None:
        owner = _FakeWindowsApi()
        other = _FakeWindowsApi(sid=OTHER_SID, world=owner.world)

        with WorkerInstanceGuard("worker-001", 0, api=owner, lock_directory=LOCKS):
            self.assertTrue(
                WorkerInstanceGuard(
                    "worker-001", 0, api=other, lock_directory=LOCKS
                ).acquire()
            )

    def test_another_windows_session_of_the_same_user_is_still_blocked(self) -> None:
        console = _FakeWindowsApi(session=1)
        remote = _FakeWindowsApi(session=2, world=console.world)

        with WorkerInstanceGuard("worker-001", 0, api=console, lock_directory=LOCKS):
            second = WorkerInstanceGuard(
                "worker-001", 0, api=remote, lock_directory=LOCKS
            )

            # The session-local mutex does not see the other session at all; the
            # share-none file lock is what stops a second registration.
            self.assertFalse(second.acquire())
            self.assertEqual(second.issue_code, "WORKER_ALREADY_RUNNING")

    def test_a_cross_session_rejection_leaks_no_handle(self) -> None:
        console = _FakeWindowsApi(session=1)
        remote = _FakeWindowsApi(session=2, world=console.world)

        with WorkerInstanceGuard("worker-001", 0, api=console, lock_directory=LOCKS):
            WorkerInstanceGuard(
                "worker-001", 0, api=remote, lock_directory=LOCKS
            ).acquire()

            self.assertEqual(remote.leaked, [])

    def test_a_rejected_guard_leaks_no_handle(self) -> None:
        api = _FakeWindowsApi()

        with WorkerInstanceGuard("worker-001", 0, api=api, lock_directory=LOCKS):
            WorkerInstanceGuard("worker-001", 0, api=api, lock_directory=LOCKS).acquire()
            self.assertEqual(len(api.leaked), 2)

        self.assertEqual(api.leaked, [])

    def test_release_frees_the_identity_for_a_later_run(self) -> None:
        api = _FakeWindowsApi()
        first = WorkerInstanceGuard("worker-001", 0, api=api, lock_directory=LOCKS)
        self.assertTrue(first.acquire())

        first.release()

        self.assertFalse(first.acquired)
        self.assertTrue(
            WorkerInstanceGuard(
                "worker-001", 0, api=api, lock_directory=LOCKS
            ).acquire()
        )
        self.assertEqual(api.leaked, api.opened[-2:])

    def test_release_is_idempotent_and_acquire_is_not_re_entrant(self) -> None:
        api = _FakeWindowsApi()
        guard = WorkerInstanceGuard("worker-001", 0, api=api, lock_directory=LOCKS)

        self.assertTrue(guard.acquire())
        self.assertTrue(guard.acquire())
        guard.release()
        guard.release()

        self.assertEqual(len(api.opened), 2)
        self.assertEqual(len(api.closed), 2)
        self.assertEqual(api.leaked, [])

    def test_a_rejected_guard_refuses_to_enter_a_context(self) -> None:
        api = _FakeWindowsApi()

        with WorkerInstanceGuard("worker-001", 0, api=api, lock_directory=LOCKS):
            with self.assertRaises(Exception) as caught:
                with WorkerInstanceGuard("worker-001", 0, api=api, lock_directory=LOCKS):
                    self.fail("a duplicate instance must not enter the context")

        self.assertIn("WORKER_ALREADY_RUNNING", str(caught.exception))

    def test_gpu_index_and_worker_id_are_validated_before_any_handle(self) -> None:
        api = _FakeWindowsApi()
        invalid = (("", 0), ("   ", 0), ("worker-001", -1), ("worker-001", True))

        for worker_id, gpu_index in invalid:
            with self.subTest(worker_id=worker_id, gpu_index=gpu_index):
                with self.assertRaises(ValueError):
                    WorkerInstanceGuard(worker_id, gpu_index, api=api, lock_directory=LOCKS)

        self.assertEqual(api.opened, [])


@unittest.skipUnless(os.name == "nt", "the instance guard is Windows-only")
class WindowsWorkerInstanceGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.locks = self._directory.name
        self.worker_id = f"worker-test-{uuid4().hex[:12]}"

    def _guard(self, gpu_index: int = 7) -> WorkerInstanceGuard:
        return WorkerInstanceGuard(
            self.worker_id, gpu_index, lock_directory=self.locks
        )

    def test_a_real_second_instance_is_rejected_and_released_on_exit(self) -> None:
        with self._guard():
            self.assertFalse(self._guard().acquire())

        second = self._guard()
        self.assertTrue(second.acquire())
        second.release()

    def test_a_real_different_gpu_index_is_allowed(self) -> None:
        with self._guard(gpu_index=7):
            other = self._guard(gpu_index=8)
            self.assertTrue(other.acquire())
            other.release()

    def test_the_operating_system_reclaims_the_mutex_after_an_abnormal_exit(self) -> None:
        program = (
            "import sys, time\n"
            "from gpu_worker.instance_guard import WorkerInstanceGuard\n"
            f"guard = WorkerInstanceGuard({self.worker_id!r}, 9,"
            f" lock_directory={self.locks!r})\n"
            "assert guard.acquire()\n"
            "print('ACQUIRED', flush=True)\n"
            "time.sleep(60)\n"
        )
        environment = {**os.environ, "PYTHONPATH": os.getcwd()}
        holder = subprocess.Popen(
            [sys.executable, "-c", program],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
            text=True,
        )
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)

        self.assertEqual(holder.stdout.readline().strip(), "ACQUIRED")
        blocked = self._guard(gpu_index=9)
        self.assertFalse(blocked.acquire())

        holder.kill()
        holder.wait(timeout=10)

        # Windows frees the named object shortly after the process handle signals,
        # with no cleanup code of ours involved, so poll rather than assume.
        deadline = time.monotonic() + 10
        while True:
            reclaimed = self._guard(gpu_index=9)
            if reclaimed.acquire():
                reclaimed.release()
                return
            self.assertLess(time.monotonic(), deadline, "the mutex was never reclaimed")
            time.sleep(0.1)


if __name__ == "__main__":
    unittest.main()
