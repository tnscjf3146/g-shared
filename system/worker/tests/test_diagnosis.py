"""What each answer from the machine is judged to mean.

The probe's job is to report what Windows said; this file's job is what to
conclude from it. Both directions matter, so every prerequisite is tested for
the verdict AND for what the supplier is told to do about it.
"""

from __future__ import annotations

import unittest

from gshared_worker.diagnosis import (
    MINIMUM_WINDOWS_BUILD,
    EnvironmentDiagnosis,
    Finding,
    Prerequisite,
)
from gshared_worker.windows_probe import NvidiaDriver

HEALTHY = dict(
    build=MINIMUM_WINDOWS_BUILD,
    virtualization=True,
    driver=NvidiaDriver("556.12", "NVIDIA GeForce RTX 4060"),
    wsl_version=2,
    wsl_named=True,
)


class FakeProbe:
    """A machine that answers exactly what a test says it answers."""

    def __init__(self, **answers):
        self.answers = {**HEALTHY, **answers}
        self.asked = []

    async def windows_build(self):
        self.asked.append("windows_build")
        return self.answers["build"]

    async def firmware_virtualization_enabled(self):
        self.asked.append("firmware_virtualization_enabled")
        return self.answers["virtualization"]

    async def nvidia_driver(self):
        self.asked.append("nvidia_driver")
        return self.answers["driver"]

    async def wsl_default_version(self):
        self.asked.append("wsl_default_version")
        return self.answers["wsl_version"]

    async def wsl_can_create_named_distributions(self):
        self.asked.append("wsl_can_create_named_distributions")
        return self.answers["wsl_named"]


class DiagnosisTestCase(unittest.IsolatedAsyncioTestCase):
    async def diagnose(self, **answers):
        self.probe = FakeProbe(**answers)
        return await EnvironmentDiagnosis(self.probe).run()

    async def check(self, prerequisite, **answers):
        return (await self.diagnose(**answers)).find(prerequisite)


class AHealthyMachineTest(DiagnosisTestCase):
    async def test_everything_is_satisfied(self):
        found = await self.diagnose()

        self.assertTrue(found.is_ready)

    async def test_every_prerequisite_gets_exactly_one_answer(self):
        found = await self.diagnose()

        self.assertEqual(
            [c.prerequisite for c in found.checks], list(Prerequisite)
        )

    async def test_nothing_is_left_for_the_person_to_do(self):
        found = await self.diagnose()

        self.assertEqual(found.blocked, ())
        self.assertEqual(found.resolvable, ())
        self.assertEqual(found.undetermined, ())


class WindowsVersionTest(DiagnosisTestCase):
    async def test_a_supported_build_is_satisfied_and_says_which(self):
        check = await self.check(Prerequisite.WINDOWS_VERSION, build=22631)

        self.assertIs(check.finding, Finding.SATISFIED)
        self.assertIn("22631", check.detail)

    async def test_the_oldest_supported_build_is_still_supported(self):
        check = await self.check(
            Prerequisite.WINDOWS_VERSION, build=MINIMUM_WINDOWS_BUILD
        )

        self.assertIs(check.finding, Finding.SATISFIED)

    async def test_one_build_older_is_blocked(self):
        # Below this the GPU cannot be projected into WSL2 at all, so no amount
        # of installing helps -- only a Windows update.
        check = await self.check(
            Prerequisite.WINDOWS_VERSION, build=MINIMUM_WINDOWS_BUILD - 1
        )

        self.assertIs(check.finding, Finding.BLOCKED)
        self.assertIn("Windows를 업데이트", check.remedy)

    async def test_not_being_windows_is_blocked_rather_than_missing(self):
        check = await self.check(Prerequisite.WINDOWS_VERSION, build=None)

        self.assertIs(check.finding, Finding.BLOCKED)
        self.assertIn("Windows가 아닙니다", check.detail)


class HardwareVirtualizationTest(DiagnosisTestCase):
    async def test_a_machine_that_can_virtualize_is_satisfied(self):
        check = await self.check(Prerequisite.HARDWARE_VIRTUALIZATION, virtualization=True)

        self.assertIs(check.finding, Finding.SATISFIED)

    async def test_it_does_not_claim_the_firmware_switch_is_on(self):
        # On a machine already running a hypervisor the firmware flag reads
        # False. Saying "enabled in firmware" would print something the machine
        # itself contradicts.
        check = await self.check(Prerequisite.HARDWARE_VIRTUALIZATION, virtualization=True)

        self.assertNotIn("firmware", check.detail.casefold())

    async def test_virtualization_off_is_blocked_and_names_the_bios(self):
        check = await self.check(Prerequisite.HARDWARE_VIRTUALIZATION, virtualization=False)

        self.assertIs(check.finding, Finding.BLOCKED)
        self.assertIn("BIOS", check.remedy)

    async def test_an_unreadable_setting_is_undetermined_not_disabled(self):
        check = await self.check(Prerequisite.HARDWARE_VIRTUALIZATION, virtualization=None)

        self.assertIs(check.finding, Finding.UNDETERMINED)
        self.assertIsNone(check.remedy)


class NvidiaDriverTest(DiagnosisTestCase):
    async def test_a_driver_is_satisfied_and_names_its_version(self):
        check = await self.check(
            Prerequisite.NVIDIA_DRIVER, driver=NvidiaDriver("556.12")
        )

        self.assertIs(check.finding, Finding.SATISFIED)
        self.assertIn("556.12", check.detail)

    async def test_it_names_the_card_when_the_card_is_known(self):
        check = await self.check(
            Prerequisite.NVIDIA_DRIVER, driver=NvidiaDriver("556.12", "RTX 4060")
        )

        self.assertIn("RTX 4060", check.detail)

    async def test_a_card_whose_model_is_unknown_is_still_satisfied(self):
        check = await self.check(
            Prerequisite.NVIDIA_DRIVER, driver=NvidiaDriver("556.12", None)
        )

        self.assertIs(check.finding, Finding.SATISFIED)

    async def test_no_driver_is_blocked_because_installing_one_is_not_ours(self):
        # Driver installation reboots and is specific to the card.
        check = await self.check(Prerequisite.NVIDIA_DRIVER, driver=None)

        self.assertIs(check.finding, Finding.BLOCKED)
        self.assertIn("NVIDIA 드라이버를 설치", check.remedy)


class Wsl2Test(DiagnosisTestCase):
    async def test_a_default_of_two_is_satisfied(self):
        check = await self.check(Prerequisite.WSL2, wsl_version=2)

        self.assertIs(check.finding, Finding.SATISFIED)

    async def test_a_default_of_one_is_inactive_because_it_can_be_switched(self):
        check = await self.check(Prerequisite.WSL2, wsl_version=1)

        self.assertIs(check.finding, Finding.INACTIVE)
        self.assertIn("기본 버전을 2로", check.remedy)

    async def test_no_wsl_is_blocked_rather_than_missing(self):
        # MISSING would promise this program installs it, and "wsl --install"
        # needs an administrator and a restart. Neither is taken on the
        # supplier's behalf, so the honest answer names the command for them.
        check = await self.check(Prerequisite.WSL2, wsl_version=None)

        self.assertIs(check.finding, Finding.BLOCKED)
        self.assertIn("wsl --install", check.remedy)
        self.assertIn("다시 시작", check.remedy)

    async def test_a_wsl_too_old_to_create_our_distribution_is_blocked(self):
        check = await self.check(Prerequisite.WSL2, wsl_named=False)

        self.assertIs(check.finding, Finding.BLOCKED)
        self.assertIn("wsl --update", check.remedy)

    async def test_the_question_going_unanswered_does_not_refuse_the_machine(self):
        # None means nothing was learned, which is not the same as "too old".
        check = await self.check(Prerequisite.WSL2, wsl_named=None)

        self.assertIs(check.finding, Finding.SATISFIED)


class NothingIsBlamedThatWasNeverTestedTest(DiagnosisTestCase):
    async def test_wsl_is_not_tested_on_a_windows_that_is_too_old(self):
        found = await self.diagnose(build=MINIMUM_WINDOWS_BUILD - 1)

        self.assertIs(found.find(Prerequisite.WSL2).finding, Finding.UNDETERMINED)
        self.assertIn("WINDOWS_VERSION", found.find(Prerequisite.WSL2).detail)

    async def test_wsl_is_not_tested_when_the_firmware_forbids_virtualization(self):
        found = await self.diagnose(virtualization=False)

        self.assertIs(found.find(Prerequisite.WSL2).finding, Finding.UNDETERMINED)
        self.assertIn("HARDWARE_VIRTUALIZATION", found.find(Prerequisite.WSL2).detail)

    async def test_wsl_is_not_even_asked_in_that_case(self):
        # Not merely reported as untested: the commands must not run at all.
        await self.diagnose(virtualization=False)

        self.assertNotIn("wsl_default_version", self.probe.asked)

    async def test_an_unreadable_firmware_still_lets_wsl_be_tested(self):
        # The direct test is better evidence than the indirect one, so a
        # question that went unanswered is no reason to skip the one after it.
        found = await self.diagnose(virtualization=None)

        self.assertIs(found.find(Prerequisite.WSL2).finding, Finding.SATISFIED)

    async def test_a_missing_driver_does_not_stop_wsl_being_tested(self):
        # They are independent: a machine can want both answers at once.
        found = await self.diagnose(driver=None)

        self.assertIs(found.find(Prerequisite.WSL2).finding, Finding.SATISFIED)


class WhatTheReportIsForTest(DiagnosisTestCase):
    async def test_a_machine_with_anything_unresolved_is_not_ready(self):
        found = await self.diagnose(wsl_version=1)

        self.assertFalse(found.is_ready)

    async def test_it_separates_what_a_person_must_do_from_what_a_program_could(self):
        found = await self.diagnose(virtualization=False, wsl_version=1)

        self.assertEqual(
            [c.prerequisite for c in found.blocked],
            [Prerequisite.HARDWARE_VIRTUALIZATION],
        )
        # WSL was never tested on that machine, so it is neither.
        self.assertEqual(found.resolvable, ())

    async def test_a_wsl_that_defaults_to_one_is_something_a_program_could_fix(self):
        found = await self.diagnose(wsl_version=1)

        self.assertEqual(
            [c.prerequisite for c in found.resolvable], [Prerequisite.WSL2]
        )

    async def test_asking_for_a_prerequisite_that_does_not_exist_is_an_error(self):
        found = await self.diagnose()

        with self.assertRaises(KeyError):
            found.find("DOCKER_ENGINE")


if __name__ == "__main__":
    unittest.main()
