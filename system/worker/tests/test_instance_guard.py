"""One Worker per user, per Worker ID, per GPU.

The fake models both handles the real guard takes: a session-local mutex, and a
share-none file the whole file system shares. Everything about who may run is
decided against that fake, so the rules can be tested on any machine; the real
Windows handles are exercised end to end by test_worker_startup.py.
"""

from __future__ import annotations

import unittest

from gshared_worker.errors import WorkerAlreadyRunningError
from gshared_worker.instance_guard import (
    ISSUE_CODE,
    WorkerInstanceGuard,
    default_lock_directory,
    worker_instance_digest,
    worker_instance_mutex_name,
)

OWNER_SID = "S-1-5-21-1111111111-2222222222-3333333333-1001"
OTHER_SID = "S-1-5-21-0000000000-0000000000-0000000000-2002"
LOCKS = "C:\\fake-state"


class FakeWindowsApi:
    """Each name is taken until its owner closes it.

    ``session`` separates the session-local mutex namespace from the file
    system, which every Windows session of one user shares.
    """

    def __init__(self, sid=OWNER_SID, *, session=1, world=None):
        self._sid = sid
        self._session = session
        shared = world if world is not None else {"mutexes": {}, "files": {}}
        self._mutexes = shared["mutexes"]
        self._files = shared["files"]
        self.world = shared
        self._names = {}
        self._next_handle = 1
        self.opened = []
        self.closed = []

    def current_user_sid(self):
        return self._sid

    def create_named_mutex(self, name):
        handle = self._new_handle()
        key = (self._session, name)
        if key in self._mutexes:
            return handle, True
        self._mutexes[key] = handle
        self._names[handle] = ("mutexes", key)
        return handle, False

    def open_share_none_lock(self, path):
        key = str(path)
        if key in self._files:
            return None
        handle = self._new_handle()
        self._files[key] = handle
        self._names[handle] = ("files", key)
        return handle

    def close_handle(self, handle):
        self.closed.append(handle)
        entry = self._names.pop(handle, None)
        if entry is not None:
            store, key = entry
            self.world[store].pop(key, None)

    def _new_handle(self):
        handle = self._next_handle
        self._next_handle += 1
        self.opened.append(handle)
        return handle

    @property
    def leaked(self):
        return [handle for handle in self.opened if handle not in self.closed]


def guard(api, worker_id="worker-001", gpu_index=0):
    return WorkerInstanceGuard(
        worker_id, gpu_index, api=api, lock_directory=LOCKS
    )


class OnlyOneWorkerServesAGpuTest(unittest.TestCase):
    def test_the_first_worker_takes_the_identity(self):
        first = guard(FakeWindowsApi())

        self.assertTrue(first.acquire())
        self.assertTrue(first.acquired)
        self.assertIsNone(first.issue_code)

    def test_a_second_worker_in_the_same_session_is_refused(self):
        world = {"mutexes": {}, "files": {}}
        first = guard(FakeWindowsApi(world=world))
        first.acquire()

        second = guard(FakeWindowsApi(world=world))

        self.assertFalse(second.acquire())
        self.assertEqual(second.issue_code, ISSUE_CODE)

    def test_a_second_worker_in_another_windows_session_is_refused_too(self):
        # The mutex is session-local, so a console login and an RDP login never
        # meet it. The share-none file lock is what stops them.
        world = {"mutexes": {}, "files": {}}
        console = guard(FakeWindowsApi(session=1, world=world))
        console.acquire()

        remote = guard(FakeWindowsApi(session=2, world=world))

        self.assertFalse(remote.acquire())
        self.assertEqual(remote.issue_code, ISSUE_CODE)

    def test_the_identity_comes_back_when_the_first_worker_releases_it(self):
        world = {"mutexes": {}, "files": {}}
        first = guard(FakeWindowsApi(world=world))
        first.acquire()
        first.release()

        second = guard(FakeWindowsApi(world=world))

        self.assertTrue(second.acquire())
        self.assertIsNone(second.issue_code)

    def test_another_gpu_on_the_same_machine_is_a_different_identity(self):
        world = {"mutexes": {}, "files": {}}
        first = guard(FakeWindowsApi(world=world), gpu_index=0)
        first.acquire()

        second = guard(FakeWindowsApi(world=world), gpu_index=1)

        self.assertTrue(second.acquire())

    def test_another_user_on_the_same_machine_is_a_different_identity(self):
        world = {"mutexes": {}, "files": {}}
        mine = guard(FakeWindowsApi(sid=OWNER_SID, world=world))
        mine.acquire()

        theirs = guard(FakeWindowsApi(sid=OTHER_SID, world=world))

        self.assertTrue(theirs.acquire())

    def test_a_refused_worker_keeps_no_handle_it_was_given(self):
        world = {"mutexes": {}, "files": {}}
        guard(FakeWindowsApi(world=world)).acquire()
        api = FakeWindowsApi(world=world)

        guard(api).acquire()

        self.assertEqual(api.leaked, [])

    def test_releasing_gives_back_everything_it_took(self):
        api = FakeWindowsApi()
        held = guard(api)
        held.acquire()

        held.release()

        self.assertEqual(api.leaked, [])
        self.assertFalse(held.acquired)

    def test_acquiring_twice_does_not_take_a_second_set_of_handles(self):
        api = FakeWindowsApi()
        held = guard(api)
        held.acquire()

        self.assertTrue(held.acquire())
        self.assertEqual(len(api.opened), 2)


class UsedAsAContextManagerTest(unittest.TestCase):
    def test_it_says_so_by_name_when_another_worker_holds_the_identity(self):
        world = {"mutexes": {}, "files": {}}
        guard(FakeWindowsApi(world=world)).acquire()

        with self.assertRaises(WorkerAlreadyRunningError) as refused:
            with guard(FakeWindowsApi(world=world)):
                self.fail("the body must not run")

        self.assertIn(ISSUE_CODE, str(refused.exception))

    def test_leaving_the_block_releases_the_identity(self):
        world = {"mutexes": {}, "files": {}}
        api = FakeWindowsApi(world=world)

        with guard(api):
            pass

        self.assertEqual(api.leaked, [])
        self.assertTrue(guard(FakeWindowsApi(world=world)).acquire())


class TheNameNobodyCanReadTest(unittest.TestCase):
    def test_neither_the_worker_id_nor_the_sid_appears_in_the_mutex_name(self):
        name = worker_instance_mutex_name(OWNER_SID, "worker-001", 0)

        self.assertNotIn("worker-001", name)
        self.assertNotIn(OWNER_SID, name)

    def test_the_mutex_is_local_to_this_windows_session(self):
        self.assertTrue(
            worker_instance_mutex_name(OWNER_SID, "worker-001", 0).startswith("Local\\")
        )

    def test_the_three_parts_cannot_be_shifted_into_each_other(self):
        # Without a separator "worker-1" + "0" and "worker-" + "10" would hash
        # to the same identity, and two Workers would exclude each other.
        self.assertNotEqual(
            worker_instance_digest(OWNER_SID, "worker-1", 0),
            worker_instance_digest(OWNER_SID, "worker-", 10),
        )


class AnIdentityThatCannotBeUsedIsRefusedTest(unittest.TestCase):
    def test_an_empty_worker_id_is_refused(self):
        with self.assertRaises(ValueError):
            WorkerInstanceGuard("  ", 0, api=FakeWindowsApi(), lock_directory=LOCKS)

    def test_a_negative_gpu_index_is_refused(self):
        with self.assertRaises(ValueError):
            WorkerInstanceGuard(
                "worker-001", -1, api=FakeWindowsApi(), lock_directory=LOCKS
            )

    def test_a_boolean_is_not_a_gpu_index(self):
        with self.assertRaises(ValueError):
            WorkerInstanceGuard(
                "worker-001", True, api=FakeWindowsApi(), lock_directory=LOCKS
            )


class WhereTheLockFileLivesTest(unittest.TestCase):
    def test_it_is_under_the_users_own_local_app_data(self):
        directory = default_lock_directory({"LOCALAPPDATA": "C:\\Users\\ada\\AppData\\Local"})

        self.assertEqual(directory.parts[-2:], ("G-shared", "state"))

    def test_a_user_without_local_app_data_is_told_so(self):
        with self.assertRaises(ValueError):
            default_lock_directory({})


if __name__ == "__main__":
    unittest.main()
