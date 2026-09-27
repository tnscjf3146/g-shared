"""The WSL layer: how commands reach our distribution and what WSL says back.

Everything here drives the real parsing and the real command construction with
recorded answers, so it runs on any machine. The last class is the one that
matters most for safety: whatever happens, no command may name a distribution
other than ours.
"""

from __future__ import annotations

import unittest

from gshared_worker.command_runner import CommandResult
from gshared_worker.config import WSL_DISTRO_NAME
from gshared_worker.errors import CommandError
from gshared_worker.wsl_runtime import (
    BASE_DISTRO_PREFERENCE,
    GsharedRuntimeProbe,
    WslCommandRunner,
    WslDistroProbe,
    WslDistroProvisioner,
    default_location,
)

LIST_VERBOSE = (
    "  NAME                   STATE           VERSION\n"
    "* Ubuntu-24.04           Running         2\n"
    "  gshared-worker         Stopped         2\n"
    "  docker-desktop         Running         2\n"
)


class Recorder:
    """Answers per command and remembers every one it was asked to run."""

    def __init__(self, answers=None, default=None):
        self.answers = answers or {}
        self.default = default if default is not None else CommandResult(0, "", "")
        self.commands: list[list[str]] = []

    async def run(self, args, *, timeout, max_output_bytes):
        self.commands.append(list(args))
        for needle, answer in self.answers.items():
            if needle in " ".join(args):
                if isinstance(answer, Exception):
                    raise answer
                return answer
        if isinstance(self.default, Exception):
            raise self.default
        return self.default

    @property
    def flattened(self) -> str:
        return "\n".join(" ".join(command) for command in self.commands)


def result(stdout="", *, exit_code=0, stderr=""):
    return CommandResult(exit_code, stdout, stderr)


class HowCommandsReachTheDistributionTest(unittest.IsolatedAsyncioTestCase):
    def test_every_command_starts_with_wsl_and_names_our_distribution(self):
        # There is no path by which this reaches docker.exe on Windows.
        self.assertEqual(
            WslCommandRunner(Recorder()).prefix,
            ["wsl", "-d", WSL_DISTRO_NAME, "-u", "root", "--exec"],
        )

    def test_it_uses_exec_rather_than_a_double_dash(self):
        # "--" runs the command through the distribution's shell, which expands
        # the arguments once before the real command sees them. A
        # $VERSION_CODENAME then arrives empty and breaks the Docker apt entry.
        prefix = WslCommandRunner(Recorder()).prefix

        self.assertIn("--exec", prefix)
        self.assertNotIn("--", prefix[:-1])

    async def test_the_command_is_appended_to_the_prefix(self):
        runner = Recorder()

        await WslCommandRunner(runner).run(
            ["docker", "ps"], timeout=5, max_output_bytes=1024)

        self.assertEqual(
            runner.commands[0],
            ["wsl", "-d", WSL_DISTRO_NAME, "-u", "root", "--exec", "docker", "ps"],
        )

    async def test_the_nuls_wsl_writes_are_stripped_from_what_it_said(self):
        runner = Recorder(default=result("o\x00k\x00", stderr="b\x00a\x00d\x00"))

        answered = await WslCommandRunner(runner).run(
            ["true"], timeout=5, max_output_bytes=1024)

        self.assertEqual(answered.stdout, "ok")
        self.assertEqual(answered.stderr, "bad")


class WhatWslKnowsAboutOurDistributionTest(unittest.IsolatedAsyncioTestCase):
    async def state_from(self, listing, **kwargs):
        runner = Recorder(default=result(listing) if isinstance(listing, str) else listing)
        return await WslDistroProbe(runner, **kwargs).state()

    async def test_a_registered_wsl2_distribution_is_usable(self):
        state = await self.state_from(LIST_VERBOSE)

        self.assertTrue(state.exists)
        self.assertEqual(state.version, 2)
        self.assertTrue(state.is_usable)

    async def test_it_reads_whether_the_distribution_is_running(self):
        state = await self.state_from(LIST_VERBOSE)

        self.assertFalse(state.running)

    async def test_a_distribution_on_wsl1_exists_but_is_not_usable(self):
        # Version 1 cannot reach the GPU at all.
        listing = LIST_VERBOSE.replace("gshared-worker         Stopped         2",
                                       "gshared-worker         Stopped         1")
        state = await self.state_from(listing)

        self.assertTrue(state.exists)
        self.assertEqual(state.version, 1)
        self.assertFalse(state.is_usable)

    async def test_a_machine_without_our_distribution_says_so(self):
        listing = LIST_VERBOSE.replace("  gshared-worker         Stopped         2\n", "")

        self.assertFalse((await self.state_from(listing)).exists)

    async def test_the_default_marker_is_not_part_of_the_name(self):
        listing = LIST_VERBOSE.replace("  gshared-worker", "* gshared-worker")

        self.assertTrue((await self.state_from(listing)).exists)

    async def test_utf16_output_is_read_rather_than_seen_as_empty(self):
        spaced = "".join(c + "\x00" for c in LIST_VERBOSE)

        self.assertTrue((await self.state_from(spaced)).exists)

    async def test_a_wsl_that_would_not_answer_leaves_us_with_nothing(self):
        state = await self.state_from(CommandError("COMMAND_NOT_FOUND", "no wsl.exe"))

        self.assertFalse(state.exists)

    async def test_a_wsl_that_hangs_is_the_same_answer(self):
        state = await self.state_from(CommandError("COMMAND_TIMEOUT", "too slow"))

        self.assertFalse(state.exists)

    async def test_it_only_looks_it_never_changes_anything(self):
        runner = Recorder(default=result(LIST_VERBOSE))

        await WslDistroProbe(runner).state()

        self.assertEqual(runner.commands, [["wsl", "--list", "--verbose"]])


class WhatIsInsideTheDistributionTest(unittest.IsolatedAsyncioTestCase):
    async def probe_reads(self, stdout):
        runner = Recorder(default=result(stdout) if isinstance(stdout, str) else stdout)
        probe = GsharedRuntimeProbe(WslCommandRunner(runner))
        return await probe.engine_state(), await probe.toolkit_configured(), runner

    async def test_an_answering_daemon_is_a_running_engine(self):
        engine, _, _ = await self.probe_reads("engine=running\ntoolkit=configured\n")

        self.assertEqual(engine, "running")

    async def test_a_docker_on_path_whose_daemon_is_dead_is_only_installed(self):
        # "which docker succeeded" is not the same as an engine that answers.
        engine, _, _ = await self.probe_reads("engine=installed\ntoolkit=absent\n")

        self.assertEqual(engine, "installed")

    async def test_no_docker_at_all_is_absent(self):
        engine, _, _ = await self.probe_reads("engine=absent\ntoolkit=absent\n")

        self.assertEqual(engine, "absent")

    async def test_a_configured_toolkit_is_reported(self):
        _, toolkit, _ = await self.probe_reads("engine=running\ntoolkit=configured\n")

        self.assertTrue(toolkit)

    async def test_an_unconfigured_toolkit_is_reported(self):
        _, toolkit, _ = await self.probe_reads("engine=running\ntoolkit=absent\n")

        self.assertFalse(toolkit)

    async def test_a_distribution_that_would_not_answer_reads_as_absent(self):
        # Absent leads to an install, which is idempotent. A false all-clear
        # would send the Worker on to a runtime that is not there.
        engine, toolkit, _ = await self.probe_reads(
            CommandError("COMMAND_TIMEOUT", "the distribution did not answer"))

        self.assertEqual(engine, "absent")
        self.assertFalse(toolkit)

    async def test_it_only_looks_it_never_changes_anything(self):
        _, _, runner = await self.probe_reads("engine=running\ntoolkit=configured\n")

        # The word "installed" appears in what the script echoes back, so the
        # check is against commands that would actually change anything.
        script = runner.flattened
        for mutating in ("apt-get", "systemctl", "service docker",
                         "nvidia-ctk runtime", "dockerd", "curl"):
            self.assertNotIn(mutating, script)


class CreatingOurDistributionTest(unittest.IsolatedAsyncioTestCase):
    def provisioner(self, runner, **kwargs):
        kwargs.setdefault("location", "C:\\fake-location")
        kwargs.setdefault("inside", WslCommandRunner(runner))
        return WslDistroProvisioner(runner, **kwargs)

    async def test_a_created_distribution_is_confirmed_before_it_is_believed(self):
        # wsl.exe has been seen reporting success and registering nothing.
        runner = Recorder({
            "--list --online": result("NAME\nUbuntu-24.04\n"),
            "--list --quiet": result(f"{WSL_DISTRO_NAME}\n"),
        })

        created = await self.provisioner(runner).create()

        self.assertTrue(created.succeeded)
        self.assertIn("--list", runner.flattened)

    async def test_success_without_registration_asks_for_a_restart(self):
        runner = Recorder({
            "--list --online": result("NAME\nUbuntu-24.04\n"),
            "--list --quiet": result("Ubuntu-24.04\n"),
        })

        created = await self.provisioner(runner).create()

        self.assertFalse(created.succeeded)
        self.assertTrue(created.restart_required)
        self.assertIn("restart", created.detail.casefold())

    async def test_a_release_this_wsl_never_heard_of_moves_to_the_next_name(self):
        # Stopping at the first refusal gives up with names left untried.
        attempts = {"n": 0}

        class Fussy(Recorder):
            async def run(self, args, *, timeout, max_output_bytes):
                self.commands.append(list(args))
                joined = " ".join(args)
                if "--list --online" in joined:
                    return result("")
                if "--list --quiet" in joined:
                    return result(f"{WSL_DISTRO_NAME}\n")
                if "--install" in joined:
                    attempts["n"] += 1
                    if attempts["n"] == 1:
                        return result("", exit_code=1,
                                      stderr="WSL_E_DISTRO_NOT_FOUND")
                    return result("")
                return result("")

        created = await self.provisioner(Fussy()).create()

        self.assertTrue(created.succeeded)
        self.assertEqual(attempts["n"], 2)

    async def test_any_other_failure_stops_at_once(self):
        # A full disk is not fixed by asking for a different release, and each
        # attempt may take half an hour.
        runner = Recorder({
            "--list --online": result(""),
            "--install": result("", exit_code=1, stderr="There is not enough space"),
        })

        created = await self.provisioner(runner).create()

        self.assertFalse(created.succeeded)
        self.assertIn("space", created.detail)
        self.assertEqual(
            sum("--install" in " ".join(c) for c in runner.commands), 1)

    async def test_the_first_launch_prompt_is_never_triggered(self):
        # Without --no-launch the Ubuntu image asks for a username and
        # password, which nobody is there to answer.
        runner = Recorder({
            "--list --online": result("NAME\nUbuntu-24.04\n"),
            "--list --quiet": result(f"{WSL_DISTRO_NAME}\n"),
        })

        await self.provisioner(runner).create()

        install = next(c for c in runner.commands if "--install" in c)
        self.assertIn("--no-launch", install)
        self.assertIn("--name", install)
        self.assertIn(WSL_DISTRO_NAME, install)

    async def test_the_catalogue_decides_the_order_it_asks_in(self):
        runner = Recorder({
            "--list --online": result("NAME\nUbuntu-22.04\n"),
            "--list --quiet": result(f"{WSL_DISTRO_NAME}\n"),
        })

        await self.provisioner(runner).create()

        install = next(c for c in runner.commands if "--install" in c)
        self.assertEqual(install[2], "Ubuntu-22.04")

    async def test_a_catalogue_that_would_not_answer_still_leaves_names_to_try(self):
        runner = Recorder({
            "--list --online": result("", exit_code=1),
            "--list --quiet": result(f"{WSL_DISTRO_NAME}\n"),
        })

        created = await self.provisioner(runner).create()

        install = next(c for c in runner.commands if "--install" in c)
        self.assertTrue(created.succeeded)
        self.assertIn(install[2], BASE_DISTRO_PREFERENCE)

    async def test_the_distribution_disk_lives_under_the_users_own_profile(self):
        self.assertIn("G-shared", default_location())
        self.assertTrue(default_location().endswith(WSL_DISTRO_NAME))


class TheStepsThatChangeTheDistributionTest(unittest.IsolatedAsyncioTestCase):
    def provisioner(self, runner):
        return WslDistroProvisioner(
            runner, location="C:\\fake-location", inside=WslCommandRunner(runner))

    async def test_a_wsl1_distribution_is_converted_by_name(self):
        # Only ours, and only the one that is wrong. Not the Windows-wide
        # default, which would change what every future distribution lands on.
        runner = Recorder({"--list --verbose": result(LIST_VERBOSE)})

        step = await self.provisioner(runner).convert_to_wsl2()

        self.assertTrue(step.succeeded)
        self.assertIn(["wsl", "--set-version", WSL_DISTRO_NAME, "2"], runner.commands)

    async def test_the_conversion_is_confirmed_before_it_is_believed(self):
        runner = Recorder({"--list --verbose": result(LIST_VERBOSE)})

        await self.provisioner(runner).convert_to_wsl2()

        self.assertIn(["wsl", "--list", "--verbose"], runner.commands)

    async def test_exit_zero_that_left_it_on_version_one_is_not_success(self):
        # A distribution still on version 1 has no GPU at all. Believing the
        # exit code would hand the Worker a runtime that cannot do its job.
        still_one = LIST_VERBOSE.replace(
            "gshared-worker         Stopped         2",
            "gshared-worker         Stopped         1")
        runner = Recorder({"--list --verbose": result(still_one)})

        step = await self.provisioner(runner).convert_to_wsl2()

        self.assertFalse(step.succeeded)
        self.assertIn("still", step.detail)

    async def test_a_conversion_that_failed_outright_is_reported(self):
        runner = Recorder({
            "--set-version": result("", exit_code=1, stderr="conversion failed"),
            "--list --verbose": result(LIST_VERBOSE),
        })

        step = await self.provisioner(runner).convert_to_wsl2()

        self.assertFalse(step.succeeded)
        self.assertIn("conversion failed", step.detail)

    async def test_a_conversion_never_names_another_distribution(self):
        runner = Recorder({"--list --verbose": result(LIST_VERBOSE)})

        await self.provisioner(runner).convert_to_wsl2()

        for command in runner.commands:
            if "--set-version" in command:
                self.assertEqual(command[2], WSL_DISTRO_NAME)

    async def test_giving_the_distribution_an_init_system_restarts_it(self):
        # The setting only takes effect once the distribution has restarted.
        runner = Recorder()

        step = await self.provisioner(runner).configure_boot()

        self.assertTrue(step.succeeded)
        self.assertIn("/etc/wsl.conf", runner.flattened)
        self.assertIn(["wsl", "--terminate", WSL_DISTRO_NAME], runner.commands)

    async def test_a_failed_write_never_reports_success(self):
        runner = Recorder(default=result("", exit_code=1, stderr="read-only"))

        self.assertFalse((await self.provisioner(runner).configure_boot()).succeeded)

    async def test_installing_docker_runs_the_script_inside_the_distribution(self):
        runner = Recorder(default=result("gshared: docker installed\n"))

        step = await self.provisioner(runner).install_docker()

        self.assertTrue(step.succeeded)
        self.assertIn("docker installed", step.detail)
        self.assertTrue(runner.commands[0][:2] == ["wsl", "-d"])

    async def test_a_script_that_fails_never_reports_success(self):
        runner = Recorder(default=result("", exit_code=1, stderr="apt-get failed"))

        for step in (
            await self.provisioner(runner).install_docker(),
            await self.provisioner(runner).start_docker(),
            await self.provisioner(runner).install_toolkit(),
            await self.provisioner(runner).restart_docker(),
        ):
            self.assertFalse(step.succeeded)
            self.assertIn("apt-get failed", step.detail)

    async def test_the_install_scripts_leave_a_working_runtime_alone(self):
        # Reading the scripts, not running them: each one exits early when what
        # it would install is already there.
        from gshared_worker.wsl_runtime import (
            INSTALL_DOCKER, INSTALL_TOOLKIT, START_DOCKER,
        )

        self.assertIn("already present", INSTALL_DOCKER)
        self.assertIn("already configured", INSTALL_TOOLKIT)
        self.assertIn("already answering", START_DOCKER)


class NothingTouchesTheSuppliersOwnDistributionsTest(unittest.IsolatedAsyncioTestCase):
    """The safety property. Whatever happens, only our own name is acted on."""

    OTHERS = ("Ubuntu", "Ubuntu-24.04", "Debian", "docker-desktop")
    #: --set-default-version changes what every future distribution Windows
    #: creates lands on, and --set-default changes which one `wsl` opens by
    #: default. Both outlive G-shared and neither is ours to change.
    DESTRUCTIVE = ("--unregister", "--shutdown", "rm -rf",
                   "--set-default ", "--set-default-version")

    async def every_command(self) -> str:
        runner = Recorder({
            "--list --online": result("NAME\nUbuntu-24.04\n"),
            "--list --quiet": result(f"{WSL_DISTRO_NAME}\n"),
            "--list --verbose": result(LIST_VERBOSE),
        })
        provisioner = WslDistroProvisioner(
            runner, location="C:\\fake-location", inside=WslCommandRunner(runner))

        await WslDistroProbe(runner).state()
        await GsharedRuntimeProbe(WslCommandRunner(runner)).engine_state()
        await provisioner.create()
        await provisioner.convert_to_wsl2()
        await provisioner.configure_boot()
        await provisioner.install_docker()
        await provisioner.start_docker()
        await provisioner.install_toolkit()
        await provisioner.restart_docker()
        return runner.flattened

    async def test_no_command_targets_a_distribution_the_supplier_owns(self):
        flattened = await self.every_command()

        for command in flattened.splitlines():
            if command.startswith("wsl -d "):
                self.assertEqual(command.split()[2], WSL_DISTRO_NAME, command)

    async def test_the_only_distribution_ever_converted_is_ours(self):
        flattened = await self.every_command()

        for command in flattened.splitlines():
            if "--set-version" in command:
                self.assertEqual(command.split()[2], WSL_DISTRO_NAME, command)

    async def test_the_only_distribution_named_after_terminate_is_ours(self):
        flattened = await self.every_command()

        for command in flattened.splitlines():
            if "--terminate" in command:
                self.assertIn(WSL_DISTRO_NAME, command)

    async def test_nothing_unregisters_or_shuts_down_anything(self):
        flattened = await self.every_command()

        for destructive in self.DESTRUCTIVE:
            self.assertNotIn(destructive, flattened)

    async def test_no_other_distribution_is_ever_named_as_a_target(self):
        flattened = await self.every_command()

        for other in self.OTHERS:
            for command in flattened.splitlines():
                # A base image may be *installed from*, which is not the same
                # as acting on a distribution the supplier already has.
                if other in command and "--install" not in command:
                    self.assertNotIn(f"-d {other}", command)


if __name__ == "__main__":
    unittest.main()
