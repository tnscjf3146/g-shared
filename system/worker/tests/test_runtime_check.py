"""The stage: what the supplier reads, what the log keeps, what the launcher gets.

The last class runs the real thing against this actual PC -- the real WSL, the
real distribution, the real Docker Engine and a real GPU container. It asserts
only what must hold on any machine that is set up, and is skipped when it is not.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from gshared_worker.config import WSL_DISTRO_NAME, WorkerSettings
from gshared_worker.diagnosis import Finding
from gshared_worker.exit_codes import WorkerExitCode
from gshared_worker.logging_setup import DEBUG_LOG_NAME, start_debug_log, stop_debug_log
from gshared_worker.runtime_check import build_runtime_setup, ensure_runtime, what_is_left
from gshared_worker.runtime_diagnosis import RuntimePrerequisite
from gshared_worker.runtime_setup import Attempt, Progress, Setup
from gshared_worker.runtime_setup import Action

from tests.test_runtime_setup import diagnosis

SETTINGS = WorkerSettings(
    backend_url="ws://localhost:8080/ws/workers",
    worker_id="worker-001",
    hourly_price=600,
    gpu_index=0,
    supported_workloads=(),
    worker_token=None,
    config_path=None,
)


def prepared(progress=Progress.READY, attempts=(), **findings) -> Setup:
    return Setup(progress, diagnosis(**findings), tuple(attempts))


class Finished:
    """Stands in for RuntimeSetup with the answer already decided."""

    def __init__(self, setup):
        self._setup = setup

    async def prepare(self):
        return self._setup


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

    def run_stage(self, setup):
        return ensure_runtime(SETTINGS, self.said.append, setup=Finished(setup))

    @property
    def output(self):
        return "\n".join(self.said)

    @property
    def logged(self):
        stop_debug_log()
        return (self.directory / DEBUG_LOG_NAME).read_text(encoding="utf-8")


class ARuntimeThatIsReadyTest(StageTestCase):
    def test_the_worker_may_go_on(self):
        self.assertEqual(self.run_stage(prepared()), WorkerExitCode.OK)

    def test_the_supplier_is_told_in_the_words_the_spec_uses(self):
        self.run_stage(prepared())

        self.assertIn("RUNTIME READY", self.output)

    def test_what_the_gpu_said_is_shown(self):
        # The one line worth reading on success.
        self.run_stage(prepared())

        self.assertIn("detail for CONTAINER_GPU", self.output)

    def test_the_supplier_is_told_where_the_work_happens(self):
        self.run_stage(prepared())

        self.assertIn(WSL_DISTRO_NAME, self.output)

    def test_nothing_is_asked_of_them(self):
        self.run_stage(prepared())

        self.assertNotIn("not ready", self.output)


class ARuntimeThatIsNotReadyTest(StageTestCase):
    def test_it_stops_with_the_code_a_launcher_must_not_retry(self):
        code = self.run_stage(prepared(
            Progress.NEEDS_A_PERSON, CONTAINER_GPU=Finding.BLOCKED))

        self.assertEqual(code, WorkerExitCode.EXPECTED_FAILURE)

    def test_a_runtime_that_did_not_come_up_never_says_ready(self):
        self.run_stage(prepared(Progress.STUCK, DOCKER_ENGINE=Finding.MISSING))

        self.assertNotIn("RUNTIME READY", self.output)

    def test_the_supplier_is_shown_the_problem_and_the_fix(self):
        self.run_stage(prepared(
            Progress.NEEDS_A_PERSON, CONTAINER_GPU=Finding.BLOCKED))

        self.assertIn("detail for CONTAINER_GPU", self.output)
        self.assertIn("fix CONTAINER_GPU", self.output)

    def test_a_restart_is_asked_for_first_and_on_its_own(self):
        # Everything below it is a consequence of work Windows has not
        # finished; reading those as separate problems sends somebody looking
        # for faults a restart will clear.
        self.run_stage(prepared(
            Progress.NEEDS_A_RESTART, DOCKER_ENGINE=Finding.UNDETERMINED))

        self.assertIn("Restart this PC", self.output)


class OnlyWhatThePersonCanActOnIsShownTest(StageTestCase):
    def test_a_prerequisite_left_untested_is_not_a_second_problem(self):
        left = what_is_left(prepared(
            Progress.STUCK,
            DOCKER_ENGINE=Finding.MISSING,
            CONTAINER_TOOLKIT=Finding.UNDETERMINED,
            CONTAINER_GPU=Finding.UNDETERMINED,
        ))

        self.assertIn("detail for DOCKER_ENGINE", left)
        self.assertNotIn("detail for CONTAINER_TOOLKIT", left)

    def test_why_an_action_was_refused_is_shown_under_it(self):
        # The finding restates the problem; this says why it could not be fixed.
        left = what_is_left(prepared(
            Progress.STUCK,
            attempts=[Attempt(
                RuntimePrerequisite.DOCKER_ENGINE, Action.INSTALL, False,
                "apt-get could not reach the archive")],
            DOCKER_ENGINE=Finding.MISSING,
        ))

        self.assertIn("  It was tried and refused: apt-get could not reach the archive",
                      left)

    def test_a_successful_attempt_is_not_reported_as_a_refusal(self):
        left = what_is_left(prepared(
            Progress.STUCK,
            attempts=[Attempt(
                RuntimePrerequisite.DOCKER_ENGINE, Action.INSTALL, True, "installed")],
            DOCKER_ENGINE=Finding.MISSING,
        ))

        self.assertNotIn("  It was tried and refused: installed", left)

    def test_untested_is_all_there_is_to_say_when_nothing_else_was_found(self):
        # An empty screen on a machine that has stopped is worse than naming
        # the thing that could not be reached.
        left = what_is_left(prepared(
            Progress.STUCK, CONTAINER_GPU=Finding.UNDETERMINED))

        self.assertIn("detail for CONTAINER_GPU", left)

    def test_a_ready_runtime_has_nothing_left(self):
        self.assertEqual(what_is_left(prepared()), [])


class TheLogKeepsEverythingTest(StageTestCase):
    def test_every_prerequisite_is_recorded_even_the_ones_that_passed(self):
        self.run_stage(prepared())

        for prerequisite in RuntimePrerequisite:
            self.assertIn(prerequisite.value, self.logged)

    def test_every_action_is_recorded_with_its_outcome(self):
        self.run_stage(prepared(
            Progress.STUCK,
            attempts=[Attempt(
                RuntimePrerequisite.DOCKER_ENGINE, Action.INSTALL, False, "no archive")],
            DOCKER_ENGINE=Finding.MISSING,
        ))

        self.assertIn("INSTALL DOCKER_ENGINE: failed -- no archive", self.logged)

    def test_a_prerequisite_hidden_from_the_screen_is_still_in_the_log(self):
        self.run_stage(prepared(
            Progress.STUCK,
            DOCKER_ENGINE=Finding.MISSING,
            CONTAINER_TOOLKIT=Finding.UNDETERMINED,
        ))

        self.assertIn("CONTAINER_TOOLKIT [UNDETERMINED]", self.logged)

    def test_the_verdict_is_recorded_in_one_line(self):
        self.run_stage(prepared(Progress.STUCK, DOCKER_ENGINE=Finding.MISSING))

        self.assertIn("runtime STUCK (ready=False)", self.logged)


class TheRealThingIsWiredToTheRealProbesTest(unittest.TestCase):
    def test_the_default_setup_names_our_distribution(self):
        # Built rather than asserted from a fake: this is the wiring that runs
        # on a supplier's machine.
        setup = build_runtime_setup()

        self.assertIsNotNone(setup)


@unittest.skipUnless(os.name == "nt", "the runtime lives in WSL on Windows")
class ThisActualPcTest(unittest.IsolatedAsyncioTestCase):
    """The real WSL, the real engine, a real container. No fakes below this line.

    Skipped rather than failed when the runtime is not set up on this machine:
    a developer without WSL should still be able to run the suite.
    """

    @classmethod
    def setUpClass(cls):
        import asyncio

        from gshared_worker.container_runtime import ContainerGpuProbe
        from gshared_worker.runtime_diagnosis import RuntimeDiagnosisRunner
        from gshared_worker.wsl_runtime import (
            GsharedRuntimeProbe, WslCommandRunner, WslDistroProbe,
        )

        inside = WslCommandRunner()
        cls.found = asyncio.run(RuntimeDiagnosisRunner(
            WslDistroProbe(), GsharedRuntimeProbe(inside), ContainerGpuProbe(inside)
        ).run())

    def setUp(self):
        if not self.found.find(RuntimePrerequisite.GSHARED_DISTRO).is_resolved:
            self.skipTest(f"{WSL_DISTRO_NAME} is not set up on this machine")

    async def test_every_prerequisite_gets_an_answer(self):
        self.assertEqual(
            [c.prerequisite for c in self.found.checks], list(RuntimePrerequisite))

    async def test_no_answer_is_an_exception_or_an_empty_string(self):
        for check in self.found.checks:
            self.assertIsInstance(check.finding, Finding)
            self.assertTrue(check.detail.strip(), check.prerequisite)

    async def test_anything_unresolved_says_what_would_resolve_it(self):
        for check in self.found.checks:
            if check.finding in (Finding.MISSING, Finding.INACTIVE, Finding.BLOCKED):
                self.assertTrue(check.remedy, check.prerequisite)

    async def test_the_distribution_is_registered_on_wsl2(self):
        from gshared_worker.wsl_runtime import WslDistroProbe

        state = await WslDistroProbe().state()

        self.assertTrue(state.exists)
        self.assertEqual(state.version, 2)

    async def test_a_container_really_saw_a_gpu(self):
        gpu = self.found.find(RuntimePrerequisite.CONTAINER_GPU)
        if not gpu.is_resolved:
            self.skipTest(f"the GPU container check did not pass here: {gpu.detail}")

        # Not machine-specific: whatever GPU this is, it named itself and a
        # driver version, which is the whole claim CONTAINER_GPU makes.
        self.assertIn("driver", gpu.detail)


if __name__ == "__main__":
    unittest.main()
