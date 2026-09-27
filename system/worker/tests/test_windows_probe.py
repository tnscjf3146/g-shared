"""What each Windows command says, and what the probe makes of it.

Every test drives the real parsing with a recorded answer, so the rules can be
checked on any machine and without a GPU. What this PC actually reports is
exercised separately, at the end of test_environment_check.py.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from gshared_worker.command_runner import CommandResult
from gshared_worker.errors import CommandError
from gshared_worker.windows_probe import WindowsHostProbe


def runner_returning(**by_executable):
    """Answers per command, and refuses anything the probe should not run."""

    class Runner:
        def __init__(self):
            self.commands = []

        async def run(self, args, *, timeout, max_output_bytes):
            self.commands.append(args)
            answer = by_executable.get(args[0])
            if answer is None:
                raise AssertionError(f"unexpected command: {args}")
            if isinstance(answer, Exception):
                raise answer
            return answer

    return Runner()


def result(stdout="", *, exit_code=0, stderr=""):
    return CommandResult(exit_code, stdout, stderr)


class FakeNvml:
    def __init__(self, *, devices=1, driver="556.12", name="NVIDIA GeForce RTX 4060",
                 fails_on=None):
        self._devices = devices
        self._driver = driver
        self._name = name
        self._fails_on = fails_on
        self.shutdown_calls = 0

    def _maybe_fail(self, step):
        if self._fails_on == step:
            raise RuntimeError(f"{step} failed")

    def nvmlInit(self):
        self._maybe_fail("nvmlInit")

    def nvmlDeviceGetCount(self):
        self._maybe_fail("nvmlDeviceGetCount")
        return self._devices

    def nvmlSystemGetDriverVersion(self):
        self._maybe_fail("nvmlSystemGetDriverVersion")
        return self._driver

    def nvmlDeviceGetHandleByIndex(self, index):
        self._maybe_fail("nvmlDeviceGetHandleByIndex")
        return object()

    def nvmlDeviceGetName(self, handle):
        self._maybe_fail("nvmlDeviceGetName")
        return self._name

    def nvmlShutdown(self):
        self.shutdown_calls += 1


class WindowsVersionTest(unittest.IsolatedAsyncioTestCase):
    async def test_it_reports_the_build_of_the_windows_it_runs_on(self):
        with patch("gshared_worker.windows_probe.os.name", "nt"):
            build = await WindowsHostProbe(runner_returning()).windows_build()

        self.assertIsInstance(build, int)
        self.assertGreater(build, 0)

    async def test_anything_that_is_not_windows_has_no_build_at_all(self):
        # None is the honest answer: not "an old Windows", but "not Windows".
        with patch("gshared_worker.windows_probe.os.name", "posix"):
            self.assertIsNone(await WindowsHostProbe(runner_returning()).windows_build())


class VirtualizationTest(unittest.IsolatedAsyncioTestCase):
    async def windows_reports(self, *, firmware, hypervisor):
        probe = WindowsHostProbe(
            runner_returning(powershell=result(f"{firmware}|{hypervisor}\r\n"))
        )
        return await probe.firmware_virtualization_enabled()

    async def test_the_firmware_switch_being_on_is_enough(self):
        self.assertIs(await self.windows_reports(firmware="True", hypervisor="False"), True)

    async def test_a_running_hypervisor_settles_it_whatever_the_firmware_says(self):
        # Hyper-V claims the CPU extensions, so the firmware flag reads False on
        # every machine already running WSL2. Believing it would refuse exactly
        # the machines that work.
        self.assertIs(await self.windows_reports(firmware="False", hypervisor="True"), True)

    async def test_only_both_saying_no_is_a_real_no(self):
        self.assertIs(await self.windows_reports(firmware="False", hypervisor="False"), False)

    async def test_a_processor_that_reports_nothing_is_unreadable_not_disabled(self):
        # Guessing here either sends someone into their BIOS for nothing or
        # waves through a machine that cannot run WSL2 at all.
        self.assertIsNone(await self.windows_reports(firmware="", hypervisor="False"))

    async def test_powershell_failing_is_unreadable_too(self):
        probe = WindowsHostProbe(
            runner_returning(powershell=result("True|True", exit_code=1))
        )

        self.assertIsNone(await probe.firmware_virtualization_enabled())

    async def test_a_machine_without_powershell_is_unreadable(self):
        probe = WindowsHostProbe(
            runner_returning(powershell=CommandError("COMMAND_NOT_FOUND", "no powershell"))
        )

        self.assertIsNone(await probe.firmware_virtualization_enabled())

    async def test_powershell_hanging_is_unreadable(self):
        probe = WindowsHostProbe(
            runner_returning(powershell=CommandError("COMMAND_TIMEOUT", "too slow"))
        )

        self.assertIsNone(await probe.firmware_virtualization_enabled())


class NvidiaDriverTest(unittest.IsolatedAsyncioTestCase):
    async def test_nvml_is_asked_first_because_it_is_what_the_worker_uses(self):
        nvml = FakeNvml(driver="556.12")
        probe = WindowsHostProbe(runner_returning(), nvml_module=nvml)

        driver = await probe.nvidia_driver()

        self.assertEqual(driver.version, "556.12")

    async def test_it_reports_the_card_the_driver_answered_for(self):
        nvml = FakeNvml(name="NVIDIA GeForce RTX 4060")
        probe = WindowsHostProbe(runner_returning(), nvml_module=nvml)

        self.assertEqual((await probe.nvidia_driver()).gpu_name, "NVIDIA GeForce RTX 4060")

    async def test_the_nvml_session_is_always_closed(self):
        nvml = FakeNvml()
        probe = WindowsHostProbe(runner_returning(), nvml_module=nvml)

        await probe.nvidia_driver()

        self.assertEqual(nvml.shutdown_calls, 1)

    async def test_a_driver_with_no_card_behind_it_is_no_driver(self):
        nvml = FakeNvml(devices=0)
        probe = WindowsHostProbe(
            runner_returning(**{"nvidia-smi": result("", exit_code=1)}), nvml_module=nvml
        )

        self.assertIsNone(await probe.nvidia_driver())

    async def test_nvml_failing_is_not_the_same_as_having_no_driver(self):
        # NVML lives in a Python package. Not being able to load it says nothing
        # about the machine, and answering None would tell someone with a good
        # card to install the driver they already have.
        nvml = FakeNvml(fails_on="nvmlInit")
        probe = WindowsHostProbe(
            runner_returning(**{"nvidia-smi": result("556.12, NVIDIA GeForce RTX 4060\n")}),
            nvml_module=nvml,
        )

        driver = await probe.nvidia_driver()

        self.assertEqual(driver.version, "556.12")
        self.assertEqual(driver.gpu_name, "NVIDIA GeForce RTX 4060")

    async def test_only_both_coming_back_empty_means_no_driver(self):
        nvml = FakeNvml(fails_on="nvmlInit")
        probe = WindowsHostProbe(
            runner_returning(**{"nvidia-smi": result("", exit_code=9)}), nvml_module=nvml
        )

        self.assertIsNone(await probe.nvidia_driver())

    async def test_one_line_per_gpu_and_the_first_is_enough(self):
        nvml = FakeNvml(fails_on="nvmlInit")
        probe = WindowsHostProbe(
            runner_returning(**{"nvidia-smi": result(
                "556.12, NVIDIA GeForce RTX 4060\n556.12, NVIDIA GeForce RTX 4060\n"
            )}),
            nvml_module=nvml,
        )

        self.assertEqual((await probe.nvidia_driver()).version, "556.12")

    async def test_a_card_whose_model_cannot_be_read_still_has_a_driver(self):
        # The model is detail for the screen and the log. Losing it must never
        # turn a working driver into a missing one.
        nvml = FakeNvml(fails_on="nvmlDeviceGetName")
        probe = WindowsHostProbe(runner_returning(), nvml_module=nvml)

        driver = await probe.nvidia_driver()

        self.assertEqual(driver.version, "556.12")
        self.assertIsNone(driver.gpu_name)

    async def test_nvml_answering_in_bytes_is_read_the_same_way(self):
        nvml = FakeNvml(driver=b"556.12", name=b"NVIDIA GeForce RTX 4060")
        probe = WindowsHostProbe(runner_returning(), nvml_module=nvml)

        driver = await probe.nvidia_driver()

        self.assertEqual(driver.version, "556.12")
        self.assertEqual(driver.gpu_name, "NVIDIA GeForce RTX 4060")


class WslDefaultVersionTest(unittest.IsolatedAsyncioTestCase):
    async def wsl_says(self, stdout, *, exit_code=0):
        probe = WindowsHostProbe(runner_returning(wsl=result(stdout, exit_code=exit_code)))
        return await probe.wsl_default_version()

    async def test_it_reads_the_default_version_wsl_prints(self):
        self.assertEqual(await self.wsl_says("Default Version: 2\r\n"), 2)

    async def test_version_one_is_read_as_version_one(self):
        self.assertEqual(await self.wsl_says("Default Version: 1\r\n"), 1)

    async def test_the_app_version_is_not_mistaken_for_the_default(self):
        # "WSL version: 1.2.5.0" on a machine that defaults to 2. A looser match
        # reads that trailing 1 as the default and refuses a good machine.
        self.assertEqual(
            await self.wsl_says("WSL version: 1.2.5.0\nDefault Version: 2\n"), 2
        )

    async def test_a_newer_wsl_that_stopped_printing_it_defaults_to_two(self):
        self.assertEqual(await self.wsl_says("WSL version: 2.3.26.0\n"), 2)

    async def test_utf16_output_is_read_rather_than_seen_as_empty(self):
        # wsl.exe writes UTF-16. Decoded as UTF-8 every character is followed by
        # a NUL, so the digit is there but no plain match would find it.
        self.assertEqual(await self.wsl_says("D\x00e\x00f\x00:\x00 \x002\x00\n\x00"), 2)

    async def test_a_localised_colon_is_still_a_colon(self):
        self.assertEqual(await self.wsl_says("기본 버전：2\n"), 2)

    async def test_a_machine_without_wsl_answers_nothing(self):
        probe = WindowsHostProbe(
            runner_returning(wsl=CommandError("COMMAND_NOT_FOUND", "no wsl.exe"))
        )

        self.assertIsNone(await probe.wsl_default_version())

    async def test_wsl_failing_and_saying_nothing_is_nothing(self):
        self.assertIsNone(await self.wsl_says("", exit_code=1))

    async def test_wsl_failing_with_no_version_in_what_it_said_is_nothing(self):
        self.assertIsNone(await self.wsl_says("Windows Subsystem for Linux has no "
                                              "installed distributions.", exit_code=1))


class WslCanCreateOurOwnDistributionTest(unittest.IsolatedAsyncioTestCase):
    async def help_says(self, stdout):
        probe = WindowsHostProbe(runner_returning(wsl=result(stdout)))
        return await probe.wsl_can_create_named_distributions()

    async def test_a_wsl_that_offers_both_options_can(self):
        self.assertIs(await self.help_says("--install --name --location --no-launch"), True)

    async def test_a_wsl_missing_either_option_cannot(self):
        self.assertIs(await self.help_says("--install --no-launch"), False)
        self.assertIs(await self.help_says("--install --name"), False)

    async def test_utf16_help_is_read_rather_than_seen_as_missing_both(self):
        spaced = "".join(c + "\x00" for c in "--install --name --location")

        self.assertIs(await self.help_says(spaced), True)

    async def test_a_wsl_that_said_nothing_leaves_the_question_unanswered(self):
        # None is not "too old". Treating it as one would refuse a machine over
        # a question that was never answered.
        self.assertIsNone(await self.help_says("   "))

    async def test_no_wsl_at_all_leaves_it_unanswered(self):
        probe = WindowsHostProbe(
            runner_returning(wsl=CommandError("COMMAND_NOT_FOUND", "no wsl.exe"))
        )

        self.assertIsNone(await probe.wsl_can_create_named_distributions())


class NothingIsChangedByLookingTest(unittest.IsolatedAsyncioTestCase):
    async def test_every_command_the_probe_runs_is_a_question(self):
        # An install or a conversion slipping in here would change a supplier's
        # machine during what was advertised as a diagnosis.
        runner = runner_returning(
            powershell=result("True|True"),
            wsl=result("Default Version: 2\n--name --location"),
            **{"nvidia-smi": result("556.12, RTX 4060\n")},
        )
        probe = WindowsHostProbe(runner, nvml_module=FakeNvml(fails_on="nvmlInit"))

        await probe.firmware_virtualization_enabled()
        await probe.nvidia_driver()
        await probe.wsl_default_version()
        await probe.wsl_can_create_named_distributions()

        forbidden = ("--install", "--set-default-version", "--set-version", "--import")
        for command in runner.commands:
            self.assertNotIn(command[1] if len(command) > 1 else "", forbidden)
        self.assertEqual(
            [c[0] for c in runner.commands],
            ["powershell", "nvidia-smi", "wsl", "wsl"],
        )


if __name__ == "__main__":
    unittest.main()
