"""The stage: what the supplier reads, what the log keeps, what the launcher gets.

The last class runs the diagnosis against this actual PC. It asserts only what
must hold on any machine, so it stays a real end-to-end check without pinning
the suite to one person's hardware.
"""

from __future__ import annotations

import logging
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from gshared_worker.command_runner import AsyncSubprocessRunner
from gshared_worker.config import WorkerSettings
from gshared_worker.diagnosis import (
    Check,
    Diagnosis,
    EnvironmentDiagnosis,
    Finding,
    Prerequisite,
)
from gshared_worker.environment_check import check_environment, what_is_left
from gshared_worker.exit_codes import WorkerExitCode
from gshared_worker.logging_setup import DEBUG_LOG_NAME, start_debug_log, stop_debug_log
from gshared_worker.windows_probe import WindowsHostProbe

SETTINGS = WorkerSettings(
    backend_url="ws://localhost:8080/ws/workers",
    worker_id="worker-001",
    hourly_price=600,
    gpu_index=0,
    supported_workloads=(),
    worker_token=None,
    config_path=None,
)


def diagnosis(**findings):
    """A finished diagnosis with the findings a test cares about."""
    checks = []
    for prerequisite in Prerequisite:
        finding = findings.get(prerequisite.value, Finding.SATISFIED)
        checks.append(
            Check(
                prerequisite,
                finding,
                f"detail for {prerequisite.value}",
                None if finding is Finding.SATISFIED else f"fix {prerequisite.value}",
            )
        )
    return Diagnosis(tuple(checks))


class Finished:
    """Stands in for EnvironmentDiagnosis with the answer already decided."""

    def __init__(self, found):
        self._found = found

    async def run(self):
        return self._found


class StageTestCase(unittest.TestCase):
    def setUp(self):
        self._temporary = TemporaryDirectory()
        self.directory = Path(self._temporary.name)
        self.said = []
        start_debug_log(self.directory)
        self.addCleanup(self._clean_up)

    def _clean_up(self):
        stop_debug_log()
        self._temporary.cleanup()

    def run_stage(self, found):
        return check_environment(SETTINGS, self.said.append, diagnosis=Finished(found))

    @property
    def output(self):
        return "\n".join(self.said)

    @property
    def logged(self):
        stop_debug_log()
        return (self.directory / DEBUG_LOG_NAME).read_text(encoding="utf-8")


class AReadyMachineTest(StageTestCase):
    def test_the_worker_may_go_on(self):
        self.assertEqual(self.run_stage(diagnosis()), WorkerExitCode.OK)

    def test_the_supplier_is_told_it_is_ready(self):
        self.run_stage(diagnosis())

        self.assertIn("GPU environment ready", self.output)

    def test_nothing_is_asked_of_them(self):
        self.run_stage(diagnosis())

        self.assertNotIn("not ready", self.output)


class AMachineThatIsNotReadyTest(StageTestCase):
    def test_it_stops_with_the_code_a_launcher_must_not_retry(self):
        code = self.run_stage(diagnosis(HARDWARE_VIRTUALIZATION=Finding.BLOCKED))

        self.assertEqual(code, WorkerExitCode.EXPECTED_FAILURE)

    def test_the_supplier_is_shown_the_problem_and_the_fix(self):
        self.run_stage(diagnosis(HARDWARE_VIRTUALIZATION=Finding.BLOCKED))

        self.assertIn("detail for HARDWARE_VIRTUALIZATION", self.output)
        self.assertIn("fix HARDWARE_VIRTUALIZATION", self.output)


class OnlyWhatThePersonCanActOnIsShownTest(StageTestCase):
    def test_a_prerequisite_left_untested_is_not_a_second_problem(self):
        # One firmware switch turns into four bullet points otherwise, and the
        # only line that matters is buried among its own consequences.
        left = what_is_left(diagnosis(
            HARDWARE_VIRTUALIZATION=Finding.BLOCKED, WSL2=Finding.UNDETERMINED
        ))

        self.assertNotIn("detail for WSL2", left)
        self.assertIn("detail for HARDWARE_VIRTUALIZATION", left)

    def test_a_remedy_is_shown_under_the_thing_it_fixes(self):
        left = what_is_left(diagnosis(WSL2=Finding.INACTIVE))

        self.assertEqual(left, ["detail for WSL2", "  fix WSL2"])

    def test_untested_is_all_there_is_to_say_when_nothing_else_was_found(self):
        # An empty screen on a machine that has stopped is worse than naming
        # the thing that could not be reached.
        left = what_is_left(diagnosis(WSL2=Finding.UNDETERMINED))

        self.assertIn("detail for WSL2", left)

    def test_a_ready_machine_has_nothing_left(self):
        self.assertEqual(what_is_left(diagnosis()), [])


class TheLogKeepsEverythingTest(StageTestCase):
    def test_every_prerequisite_is_recorded_even_the_ones_that_passed(self):
        # "The driver was fine" is exactly what rules the driver out when the
        # next report arrives.
        self.run_stage(diagnosis())

        for prerequisite in Prerequisite:
            self.assertIn(prerequisite.value, self.logged)

    def test_a_prerequisite_hidden_from_the_screen_is_still_in_the_log(self):
        self.run_stage(diagnosis(
            HARDWARE_VIRTUALIZATION=Finding.BLOCKED, WSL2=Finding.UNDETERMINED
        ))

        self.assertIn("WSL2 [UNDETERMINED]", self.logged)

    def test_the_verdict_is_recorded_in_one_line(self):
        self.run_stage(diagnosis(WSL2=Finding.INACTIVE))

        self.assertIn("environment ready: False", self.logged)


@unittest.skipUnless(__import__("os").name == "nt", "the probe reads Windows")
class ThisActualPcTest(unittest.IsolatedAsyncioTestCase):
    """The real commands against the real machine. No fakes below this line."""

    async def asyncSetUp(self):
        self.found = await EnvironmentDiagnosis(
            WindowsHostProbe(AsyncSubprocessRunner())
        ).run()

    async def test_every_prerequisite_gets_an_answer(self):
        self.assertEqual(
            [c.prerequisite for c in self.found.checks], list(Prerequisite)
        )

    async def test_no_answer_is_an_exception_or_an_empty_string(self):
        for check in self.found.checks:
            self.assertIsInstance(check.finding, Finding)
            self.assertTrue(check.detail.strip(), check.prerequisite)

    async def test_anything_unresolved_says_what_would_resolve_it(self):
        for check in self.found.checks:
            if check.finding in (Finding.MISSING, Finding.INACTIVE, Finding.BLOCKED):
                self.assertTrue(check.remedy, check.prerequisite)

    async def test_this_windows_is_new_enough_to_run_the_worker(self):
        # Not machine-specific: the build floor is Windows 10 21H2, and a
        # development PC below it could not run the product at all.
        self.assertIs(
            self.found.find(Prerequisite.WINDOWS_VERSION).finding, Finding.SATISFIED
        )

    async def test_the_diagnosis_leaves_the_log_readable(self):
        logging.getLogger("gshared").info("real diagnosis: %s", self.found.is_ready)


if __name__ == "__main__":
    unittest.main()
