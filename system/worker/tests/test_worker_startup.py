"""Entry point -> configuration -> logging -> single instance, in that order.

The last class runs the real Windows handles rather than a fake, because the
whole point of the guard is that the operating system owns it: a fake can show
the rules are right, only Windows can show the handle is really taken and really
given back.
"""

from __future__ import annotations

import inspect
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from gshared_worker import environment_check
from gshared_worker import auth_check
from gshared_worker import runtime_check
from gshared_worker import worker_socket
from gshared_worker import main as worker_main
from gshared_worker.config import CONFIG_FILE_NAME
from gshared_worker.errors import WorkerAlreadyRunningError, WorkerError
from gshared_worker.exit_codes import WorkerExitCode
from gshared_worker.instance_guard import WorkerInstanceGuard
from gshared_worker.logging_setup import DEBUG_LOG_NAME, stop_debug_log

ENROLLED = {
    "backendUrl": "wss://gshared.example/ws/workers",
    "workerId": "worker-001",
    "workerToken": "a-token",
    "gpuIndex": 0,
}


class FakeGuard:
    """Takes the identity unless somebody already holds it."""

    held = set()

    def __init__(self, worker_id, gpu_index, *, lock_directory=None):
        self.identity = (worker_id, gpu_index)
        self.lock_directory = lock_directory
        self.issue_code = None
        self.released = False

    def acquire(self):
        if self.identity in FakeGuard.held:
            self.issue_code = "WORKER_ALREADY_RUNNING"
            return False
        FakeGuard.held.add(self.identity)
        return True

    def release(self):
        self.released = True
        FakeGuard.held.discard(self.identity)


class StartupTestCase(unittest.TestCase):
    """A Worker directory, an environment, and everything the run said."""

    def setUp(self):
        FakeGuard.held = set()
        self.reached = []
        self._temporary = TemporaryDirectory()
        self.directory = Path(self._temporary.name)
        self.said = []
        self.addCleanup(self._clean_up)

    def _clean_up(self):
        stop_debug_log()
        self._temporary.cleanup()

    def enrol(self, values=None):
        (self.directory / CONFIG_FILE_NAME).write_text(
            json.dumps(ENROLLED if values is None else values), encoding="utf-8"
        )

    def env(self):
        return {"LOCALAPPDATA": str(self.directory / "AppData")}

    def start(self, *, run_worker=None, guard_factory=FakeGuard, env=None):
        # The real stage diagnoses the machine it runs on, which is not what
        # these tests are about; it has its own file. Everything here needs to
        # know is whether the stage was reached at all.
        return worker_main.run(
            out=self.said.append,
            env=self.env() if env is None else env,
            directory=self.directory,
            run_worker=run_worker or self.stage_reached,
            guard_factory=guard_factory,
        )

    def stage_reached(self, settings, say):
        self.reached.append(settings)
        return WorkerExitCode.OK

    @property
    def output(self):
        return "\n".join(self.said)

    @property
    def logged(self):
        stop_debug_log()
        return (self.directory / DEBUG_LOG_NAME).read_text(encoding="utf-8")


class AConfiguredMachineStartsTest(StartupTestCase):
    def test_it_reaches_the_stage_that_follows_startup(self):
        self.enrol()

        self.assertEqual(self.start(), WorkerExitCode.OK)
        self.assertEqual(len(self.reached), 1)

    def test_that_stage_diagnoses_the_machine_then_brings_the_runtime_up(self):
        # Startup does not decide what comes next by printing something; it
        # calls whatever stage is wired in. Today that is two steps.
        self.assertIs(
            inspect.signature(worker_main.run).parameters["run_worker"].default, None
        )
        self.assertIs(worker_main.check_environment, environment_check.check_environment)
        self.assertIs(worker_main.ensure_runtime, runtime_check.ensure_runtime)

    def test_the_runtime_is_never_touched_when_the_machine_fails_diagnosis(self):
        # A machine that is not ready must not be installed into.
        self.enrol()
        reached = []

        with patch.object(worker_main, "check_environment",
                          lambda settings, say: WorkerExitCode.EXPECTED_FAILURE),              patch.object(worker_main, "ensure_runtime",
                          lambda settings, say: reached.append(1) or 0):
            code = self.start(run_worker=worker_main.start_the_machine)

        self.assertEqual(code, WorkerExitCode.EXPECTED_FAILURE)
        self.assertEqual(reached, [])

    def test_the_runtime_follows_a_machine_that_passed(self):
        self.enrol()
        reached = []

        with patch.object(worker_main, "check_environment",
                          lambda settings, say: WorkerExitCode.OK),              patch.object(worker_main, "ensure_runtime",
                          lambda settings, say: reached.append("runtime") or WorkerExitCode.OK),              patch.object(worker_main, "authenticate_worker",
                          lambda settings, say: reached.append("auth") or WorkerExitCode.OK),              patch.object(worker_main, "connect_to_backend",
                          lambda settings, say: reached.append("socket") or WorkerExitCode.OK):
            code = self.start(run_worker=worker_main.start_the_machine)

        self.assertEqual(code, WorkerExitCode.OK)
        self.assertEqual(reached, ["runtime", "auth", "socket"])

    def test_authentication_is_the_stage_after_the_runtime(self):
        self.assertIs(worker_main.authenticate_worker, auth_check.authenticate_worker)

    def test_the_backend_connection_is_the_stage_after_authentication(self):
        self.assertIs(worker_main.connect_to_backend, worker_socket.connect_to_backend)

    def test_a_machine_that_did_not_authenticate_is_never_offered_to_the_backend(self):
        # The only credential that connection accepts is the machine's own
        # token, and whether the service still accepts it is exactly what the
        # stage before it establishes. Connecting anyway would offer the
        # service a machine it has just refused.
        self.enrol()
        reached = []

        with patch.object(worker_main, "check_environment",
                          lambda settings, say: WorkerExitCode.OK),              patch.object(worker_main, "ensure_runtime",
                          lambda settings, say: WorkerExitCode.OK),              patch.object(worker_main, "authenticate_worker",
                          lambda settings, say: WorkerExitCode.CREDENTIAL_MISMATCH),              patch.object(worker_main, "connect_to_backend",
                          lambda settings, say: reached.append("socket") or 0):
            code = self.start(run_worker=worker_main.start_the_machine)

        self.assertEqual(code, WorkerExitCode.CREDENTIAL_MISMATCH)
        self.assertEqual(reached, [])

    def test_nobody_is_asked_to_sign_in_on_a_machine_without_a_runtime(self):
        # Attaching a machine to an account before knowing it can do the work
        # would register something that cannot earn.
        self.enrol()
        reached = []

        with patch.object(worker_main, "check_environment",
                          lambda settings, say: WorkerExitCode.OK),              patch.object(worker_main, "ensure_runtime",
                          lambda settings, say: WorkerExitCode.EXPECTED_FAILURE),              patch.object(worker_main, "authenticate_worker",
                          lambda settings, say: reached.append("auth") or 0):
            code = self.start(run_worker=worker_main.start_the_machine)

        self.assertEqual(code, WorkerExitCode.EXPECTED_FAILURE)
        self.assertEqual(reached, [])

    def test_the_settings_it_read_are_shown(self):
        self.enrol()

        self.start()

        self.assertIn("worker-001", self.output)
        self.assertIn("wss://gshared.example/ws/workers", self.output)

    def test_the_stage_is_handed_the_settings_it_needs(self):
        self.enrol()
        seen = []

        self.start(run_worker=lambda settings, say: seen.append(settings) or 0)

        self.assertEqual(seen[0].worker_id, "worker-001")
        self.assertEqual(seen[0].backend_url, "wss://gshared.example/ws/workers")

    def test_the_stage_decides_the_exit_code(self):
        self.enrol()

        code = self.start(
            run_worker=lambda settings, say: WorkerExitCode.TRANSPORT_FAILURE
        )

        self.assertEqual(code, WorkerExitCode.TRANSPORT_FAILURE)

    def test_the_identity_is_given_back_when_the_stage_finishes(self):
        self.enrol()
        guards = []

        def remembering(*args, **kwargs):
            guards.append(FakeGuard(*args, **kwargs))
            return guards[-1]

        self.start(guard_factory=remembering)

        self.assertTrue(guards[0].released)

    def test_the_identity_is_given_back_even_when_the_stage_fails(self):
        self.enrol()
        guards = []

        def remembering(*args, **kwargs):
            guards.append(FakeGuard(*args, **kwargs))
            return guards[-1]

        def explodes(settings, say):
            raise RuntimeError("something nobody planned for")

        self.start(run_worker=explodes, guard_factory=remembering)

        self.assertTrue(guards[0].released)


class TheLogIsStartedBeforeAnythingCanFailTest(StartupTestCase):
    def test_it_is_written_beside_the_worker(self):
        self.enrol()

        self.start()

        self.assertTrue((self.directory / DEBUG_LOG_NAME).is_file())

    def test_the_settings_are_recorded_for_whoever_diagnoses_the_run(self):
        self.enrol()

        self.start()

        self.assertIn("worker-001", self.logged)

    def test_the_stored_token_is_never_written_to_it(self):
        self.enrol()

        self.start()

        self.assertNotIn("a-token", self.logged)

    def test_a_machine_that_could_not_be_configured_still_has_a_log(self):
        self.start()

        self.assertIn("configuration refused", self.logged)


class AMachineNobodyConfiguredTest(StartupTestCase):
    def test_it_stops_with_the_code_a_launcher_must_not_retry(self):
        self.assertEqual(self.start(), WorkerExitCode.EXPECTED_FAILURE)

    def test_the_supplier_is_told_what_to_do_about_it(self):
        self.start()

        self.assertIn("not enrolled yet", self.output)

    def test_no_identity_is_claimed_before_the_settings_are_known(self):
        self.start()

        self.assertEqual(FakeGuard.held, set())

    def test_a_configuration_file_with_a_typo_is_the_same_kind_of_stop(self):
        (self.directory / CONFIG_FILE_NAME).write_text("{ not json", encoding="utf-8")

        code = self.start()

        self.assertEqual(code, WorkerExitCode.EXPECTED_FAILURE)
        self.assertIn("not valid JSON", self.output)


class ASecondWorkerOnTheSameGpuTest(StartupTestCase):
    def test_it_is_refused_by_name(self):
        self.enrol()
        FakeGuard.held.add(("worker-001", 0))

        code = self.start()

        self.assertEqual(code, WorkerExitCode.ALREADY_RUNNING)
        self.assertIn("WORKER_ALREADY_RUNNING", self.output)

    def test_no_stage_runs_behind_the_worker_that_is_already_running(self):
        self.enrol()
        FakeGuard.held.add(("worker-001", 0))

        self.start(run_worker=lambda settings, say: self.fail("must not run"))

    def test_the_stage_is_never_reached_without_the_identity(self):
        self.enrol()
        FakeGuard.held.add(("worker-001", 0))

        self.start()

        self.assertEqual(self.reached, [])

    def test_a_stage_that_finds_the_same_answer_deeper_down_agrees(self):
        self.enrol()

        def already(settings, say):
            raise WorkerAlreadyRunningError("WORKER_ALREADY_RUNNING: somewhere deeper")

        self.assertEqual(self.start(run_worker=already), WorkerExitCode.ALREADY_RUNNING)


class AFaultNobodyPlannedForTest(StartupTestCase):
    def test_it_is_a_different_code_from_a_failure_that_was_expected(self):
        self.enrol()

        def explodes(settings, say):
            raise RuntimeError("something nobody planned for")

        self.assertEqual(
            self.start(run_worker=explodes), WorkerExitCode.UNEXPECTED_FAULT
        )

    def test_the_supplier_is_pointed_at_the_file_that_holds_it(self):
        self.enrol()

        def explodes(settings, say):
            raise RuntimeError("something nobody planned for")

        self.start(run_worker=explodes)

        self.assertIn(DEBUG_LOG_NAME, self.output)

    def test_what_actually_happened_is_in_that_file(self):
        self.enrol()

        def explodes(settings, say):
            raise RuntimeError("something nobody planned for")

        self.start(run_worker=explodes)

        self.assertIn("something nobody planned for", self.logged)
        self.assertIn("Traceback", self.logged)

    def test_a_traceback_is_never_shown_to_the_supplier(self):
        self.enrol()

        def explodes(settings, say):
            raise RuntimeError("something nobody planned for")

        self.start(run_worker=explodes)

        self.assertNotIn("Traceback", self.output)


class AFailureThatWasAnticipatedTest(StartupTestCase):
    def test_it_is_explained_rather_than_reported_as_a_fault(self):
        self.enrol()

        def refuses(settings, say):
            raise WorkerError("this machine has no NVIDIA GPU")

        code = self.start(run_worker=refuses)

        self.assertEqual(code, WorkerExitCode.EXPECTED_FAILURE)
        self.assertIn("this machine has no NVIDIA GPU", self.output)


class SomebodyStoppedItTest(StartupTestCase):
    def test_ctrl_c_is_not_a_fault(self):
        self.enrol()

        def interrupted(settings, say):
            raise KeyboardInterrupt

        code = self.start(run_worker=interrupted)

        self.assertEqual(code, WorkerExitCode.OK)
        self.assertIn("Stopped.", self.output)


class TheWindowStaysOpenLongEnoughToReadTest(unittest.TestCase):
    def test_a_run_that_worked_does_not_ask_anybody_to_press_a_key(self):
        said = []

        worker_main._pause_if_the_window_will_close(
            0, owns_console=lambda: True, wait=lambda: None, out=said.append
        )

        self.assertEqual(said, [])

    def test_a_failed_run_in_a_window_of_its_own_is_held(self):
        said = []
        waited = []

        worker_main._pause_if_the_window_will_close(
            2,
            owns_console=lambda: True,
            wait=lambda: waited.append(True),
            out=said.append,
        )

        self.assertTrue(waited)
        self.assertIn("Press Enter to close this window.", said)

    def test_a_failed_run_in_somebody_elses_terminal_is_not_held(self):
        waited = []

        worker_main._pause_if_the_window_will_close(
            2,
            owns_console=lambda: False,
            wait=lambda: waited.append(True),
            out=lambda *_: None,
        )

        self.assertEqual(waited, [])

    def test_being_unable_to_hold_the_window_is_not_worth_failing_over(self):
        def cannot_tell():
            raise OSError("no console")

        worker_main._pause_if_the_window_will_close(
            2, owns_console=cannot_tell, wait=lambda: None, out=lambda *_: None
        )


@unittest.skipUnless(os.name == "nt", "the instance guard is Windows-only")
class WindowsReallyHoldsTheIdentityTest(StartupTestCase):
    """The three runs the story asks for, against the real Windows handles."""

    def setUp(self):
        super().setUp()
        self.enrol()
        self.locks = self.directory / "AppData"

    def test_the_first_run_is_the_only_worker_and_the_second_is_refused(self):
        held = WorkerInstanceGuard("worker-001", 0, lock_directory=self.locks)
        self.assertTrue(held.acquire())
        self.addCleanup(held.release)

        code = self.start(guard_factory=WorkerInstanceGuard)

        self.assertEqual(code, WorkerExitCode.ALREADY_RUNNING)
        self.assertIn("WORKER_ALREADY_RUNNING", self.output)

    def test_the_identity_is_free_again_once_the_first_worker_stops(self):
        held = WorkerInstanceGuard("worker-001", 0, lock_directory=self.locks)
        held.acquire()
        held.release()

        self.assertEqual(
            self.start(guard_factory=WorkerInstanceGuard), WorkerExitCode.OK
        )

    def test_a_run_that_finished_leaves_the_identity_for_the_next_one(self):
        self.assertEqual(
            self.start(guard_factory=WorkerInstanceGuard), WorkerExitCode.OK
        )

        self.said.clear()

        self.assertEqual(
            self.start(guard_factory=WorkerInstanceGuard), WorkerExitCode.OK
        )

    def test_the_lock_file_lives_where_the_environment_says(self):
        self.start(guard_factory=WorkerInstanceGuard)

        locks = list((self.locks / "G-shared" / "state").glob("worker-*.lock"))
        self.assertEqual(len(locks), 1)


class TheWorkerDirectoryTest(unittest.TestCase):
    def test_a_packaged_worker_reads_the_directory_it_was_copied_to(self):
        # Explorer does not set the working directory to the executable's own,
        # so the configuration would otherwise be looked for in the wrong place.
        from gshared_worker.config import worker_directory

        with patch.object(sys, "frozen", True, create=True), patch.object(
            sys, "executable", "C:\\G-shared\\G-shared.exe"
        ):
            self.assertEqual(worker_directory(), Path("C:\\G-shared"))


if __name__ == "__main__":
    unittest.main()
