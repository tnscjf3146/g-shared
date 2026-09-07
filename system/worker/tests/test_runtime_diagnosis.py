"""What each runtime answer is judged to mean, and what is left untested.

The probes report what the distribution said; this file is about what to
conclude from it -- and, just as importantly, about what is *not* concluded
when an earlier prerequisite already failed.
"""

from __future__ import annotations

import unittest

from gshared_worker.container_runtime import GpuOutcome, GpuReport, GpuVerification
from gshared_worker.diagnosis import Finding
from gshared_worker.runtime_diagnosis import (
    RuntimeDiagnosisRunner,
    RuntimePrerequisite,
)
from gshared_worker.wsl_runtime import DistroState

HEALTHY_GPU = GpuVerification(
    GpuOutcome.VISIBLE,
    "NVIDIA GeForce RTX 4070 Laptop GPU (driver 596.36, 8188 MiB)",
    GpuReport("NVIDIA GeForce RTX 4070 Laptop GPU", "596.36", 8188),
)


class FakeDistro:
    def __init__(self, state):
        self.state_value = state
        self.asked = False

    async def state(self):
        self.asked = True
        return self.state_value


class FakeRuntime:
    def __init__(self, engine="running", toolkit=True):
        self.engine = engine
        self.toolkit = toolkit
        self.asked: list[str] = []

    async def engine_state(self):
        self.asked.append("engine")
        return self.engine

    async def toolkit_configured(self):
        self.asked.append("toolkit")
        return self.toolkit


class FakeGpu:
    def __init__(self, verification=HEALTHY_GPU):
        self.verification = verification
        self.asked = False

    async def verify(self):
        self.asked = True
        return self.verification


class DiagnosisTestCase(unittest.IsolatedAsyncioTestCase):
    async def diagnose(
        self, *, distro=None, engine="running", toolkit=True, gpu=HEALTHY_GPU
    ):
        self.distro = FakeDistro(
            distro if distro is not None else DistroState(exists=True, version=2))
        self.runtime = FakeRuntime(engine, toolkit)
        self.gpu = FakeGpu(gpu)
        return await RuntimeDiagnosisRunner(
            self.distro, self.runtime, self.gpu).run()

    async def check(self, prerequisite, **kwargs):
        return (await self.diagnose(**kwargs)).find(prerequisite)


class AWorkingRuntimeTest(DiagnosisTestCase):
    async def test_everything_is_satisfied(self):
        self.assertTrue((await self.diagnose()).is_ready)

    async def test_every_prerequisite_gets_exactly_one_answer(self):
        found = await self.diagnose()

        self.assertEqual(
            [c.prerequisite for c in found.checks], list(RuntimePrerequisite))

    async def test_nothing_is_left_to_do(self):
        found = await self.diagnose()

        self.assertEqual(found.resolvable, ())
        self.assertEqual(found.blocked, ())
        self.assertEqual(found.undetermined, ())


class OurDistributionTest(DiagnosisTestCase):
    async def test_a_registered_wsl2_distribution_is_satisfied(self):
        check = await self.check(RuntimePrerequisite.GSHARED_DISTRO)

        self.assertIs(check.finding, Finding.SATISFIED)

    async def test_a_distribution_that_is_not_there_is_ours_to_create(self):
        check = await self.check(
            RuntimePrerequisite.GSHARED_DISTRO, distro=DistroState(exists=False))

        self.assertIs(check.finding, Finding.MISSING)
        self.assertIn("does not exist", check.detail)

    async def test_the_remedy_promises_not_to_touch_anything_else(self):
        check = await self.check(
            RuntimePrerequisite.GSHARED_DISTRO, distro=DistroState(exists=False))

        self.assertIn("installed for themselves is touched", check.remedy)

    async def test_a_stopped_wsl2_distribution_is_satisfied_not_inactive(self):
        # "not running" and "on version 1" are different things. Confusing them
        # would convert a perfectly good distribution for no reason -- and a
        # stopped one starts on its own the moment a command runs inside it.
        check = await self.check(
            RuntimePrerequisite.GSHARED_DISTRO,
            distro=DistroState(exists=True, version=2, running=False))

        self.assertIs(check.finding, Finding.SATISFIED)

    async def test_a_stopped_distribution_is_still_asked_what_is_inside_it(self):
        # Running a command inside it is what starts it, so the engine check
        # must not be skipped just because it was stopped.
        await self.diagnose(distro=DistroState(exists=True, version=2, running=False))

        self.assertIn("engine", self.runtime.asked)

    async def test_a_distribution_on_wsl1_is_inactive_not_missing(self):
        # It exists. Creating it again would be the wrong action entirely.
        check = await self.check(
            RuntimePrerequisite.GSHARED_DISTRO,
            distro=DistroState(exists=True, version=1))

        self.assertIs(check.finding, Finding.INACTIVE)
        self.assertIn("version 1", check.detail)


class TheDockerEngineTest(DiagnosisTestCase):
    async def test_an_answering_daemon_is_satisfied(self):
        check = await self.check(RuntimePrerequisite.DOCKER_ENGINE, engine="running")

        self.assertIs(check.finding, Finding.SATISFIED)

    async def test_docker_present_but_not_running_is_started_not_installed(self):
        # Installing over a working Docker is how a working Docker is replaced.
        check = await self.check(RuntimePrerequisite.DOCKER_ENGINE, engine="installed")

        self.assertIs(check.finding, Finding.INACTIVE)

    async def test_no_docker_at_all_is_ours_to_install(self):
        check = await self.check(RuntimePrerequisite.DOCKER_ENGINE, engine="absent")

        self.assertIs(check.finding, Finding.MISSING)


class TheContainerToolkitTest(DiagnosisTestCase):
    async def test_a_configured_toolkit_is_satisfied(self):
        check = await self.check(RuntimePrerequisite.CONTAINER_TOOLKIT, toolkit=True)

        self.assertIs(check.finding, Finding.SATISFIED)

    async def test_an_unconfigured_toolkit_is_ours_to_install(self):
        check = await self.check(RuntimePrerequisite.CONTAINER_TOOLKIT, toolkit=False)

        self.assertIs(check.finding, Finding.MISSING)


class TheGpuInsideAContainerTest(DiagnosisTestCase):
    async def test_a_gpu_that_named_itself_is_satisfied(self):
        check = await self.check(RuntimePrerequisite.CONTAINER_GPU)

        self.assertIs(check.finding, Finding.SATISFIED)
        self.assertIn("RTX 4070", check.detail)

    async def test_a_container_that_could_not_see_the_gpu_needs_a_person(self):
        # Nothing this program installs will fix a driver that has no WSL
        # support, so it is BLOCKED rather than something to retry.
        check = await self.check(
            RuntimePrerequisite.CONTAINER_GPU,
            gpu=GpuVerification(GpuOutcome.GPU_UNAVAILABLE, "no GPU in the container"))

        self.assertIs(check.finding, Finding.BLOCKED)
        self.assertIn("driver supports WSL", check.remedy)

    async def test_a_failed_image_pull_is_not_blamed_on_the_gpu(self):
        check = await self.check(
            RuntimePrerequisite.CONTAINER_GPU,
            gpu=GpuVerification(GpuOutcome.IMAGE_UNAVAILABLE, "could not be fetched"))

        self.assertIs(check.finding, Finding.UNDETERMINED)

    async def test_an_engine_that_stopped_answering_is_not_blamed_on_the_gpu(self):
        check = await self.check(
            RuntimePrerequisite.CONTAINER_GPU,
            gpu=GpuVerification(GpuOutcome.ENGINE_UNAVAILABLE, "no engine"))

        self.assertIs(check.finding, Finding.UNDETERMINED)


class NothingIsBlamedThatWasNeverTestedTest(DiagnosisTestCase):
    async def test_the_engine_is_not_tested_without_a_distribution(self):
        found = await self.diagnose(distro=DistroState(exists=False))

        engine = found.find(RuntimePrerequisite.DOCKER_ENGINE)
        self.assertIs(engine.finding, Finding.UNDETERMINED)
        self.assertIn("GSHARED_DISTRO", engine.detail)

    async def test_it_is_not_even_asked_in_that_case(self):
        # Not merely reported as untested: the command must not run at all,
        # because it would fail complaining about the wrong thing.
        await self.diagnose(distro=DistroState(exists=False))

        self.assertEqual(self.runtime.asked, [])

    async def test_the_toolkit_is_not_tested_without_an_engine(self):
        found = await self.diagnose(engine="absent")

        toolkit = found.find(RuntimePrerequisite.CONTAINER_TOOLKIT)
        self.assertIs(toolkit.finding, Finding.UNDETERMINED)
        self.assertIn("DOCKER_ENGINE", toolkit.detail)

    async def test_no_container_is_started_when_the_toolkit_is_missing(self):
        # Starting one would cost an image pull to learn what is already known.
        await self.diagnose(toolkit=False)

        self.assertFalse(self.gpu.asked)

    async def test_a_missing_distribution_skips_everything_after_it(self):
        found = await self.diagnose(distro=DistroState(exists=False))

        self.assertEqual(len(found.undetermined), 3)
        self.assertFalse(self.gpu.asked)

    async def test_the_one_thing_to_fix_is_the_only_thing_resolvable(self):
        found = await self.diagnose(distro=DistroState(exists=False))

        self.assertEqual(
            [c.prerequisite for c in found.resolvable],
            [RuntimePrerequisite.GSHARED_DISTRO],
        )


class WhatTheReportIsForTest(DiagnosisTestCase):
    async def test_anything_unresolved_means_the_runtime_is_not_ready(self):
        self.assertFalse((await self.diagnose(toolkit=False)).is_ready)

    async def test_it_separates_what_a_person_must_do_from_what_we_can(self):
        found = await self.diagnose(
            gpu=GpuVerification(GpuOutcome.GPU_UNAVAILABLE, "no GPU"))

        self.assertEqual(
            [c.prerequisite for c in found.blocked],
            [RuntimePrerequisite.CONTAINER_GPU],
        )
        self.assertEqual(found.resolvable, ())

    async def test_asking_for_a_prerequisite_that_does_not_exist_is_an_error(self):
        found = await self.diagnose()

        with self.assertRaises(KeyError):
            found.find("WINDOWS_VERSION")


if __name__ == "__main__":
    unittest.main()
