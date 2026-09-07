"""Acting on what is missing -- and, above all, not acting on what is not.

The two properties this file exists to hold:

1. **A runtime that is already good is never touched.** No install, no start,
   no restart. That is what makes the second run cost nothing.
2. **Nothing is READY because a command returned zero.** The diagnosis is run
   again afterwards, and that answer is the one that counts.
"""

from __future__ import annotations

import unittest

from gshared_worker.diagnosis import Finding
from gshared_worker.runtime_diagnosis import (
    RuntimeCheck,
    RuntimeDiagnosis,
    RuntimePrerequisite,
)
from gshared_worker.runtime_setup import (
    Action,
    ActionResult,
    GsharedProvisioner,
    Progress,
    RuntimeSetup,
)

P = RuntimePrerequisite


def diagnosis(**findings) -> RuntimeDiagnosis:
    """A finished diagnosis with the findings a test cares about."""
    checks = []
    for prerequisite in P:
        finding = findings.get(prerequisite.value, Finding.SATISFIED)
        checks.append(RuntimeCheck(
            prerequisite, finding, f"detail for {prerequisite.value}",
            None if finding is Finding.SATISFIED else f"fix {prerequisite.value}",
        ))
    return RuntimeDiagnosis(tuple(checks))


class Diagnoses:
    """Answers with each diagnosis in turn, repeating the last one."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.rounds = 0

    async def run(self):
        self.rounds += 1
        index = min(self.rounds - 1, len(self.answers) - 1)
        return self.answers[index]


class Records:
    """A provisioner that does nothing but remember what it was asked to do."""

    def __init__(self, succeeds=True, reboot_after=None):
        self.started: list[RuntimePrerequisite] = []
        self.installed: list[RuntimePrerequisite] = []
        self.succeeds = succeeds
        self.reboot_after = reboot_after

    async def start(self, prerequisite):
        self.started.append(prerequisite)
        return self._result(prerequisite)

    async def install(self, prerequisite):
        self.installed.append(prerequisite)
        return self._result(prerequisite)

    def _result(self, prerequisite):
        return ActionResult(
            self.succeeds, f"did {prerequisite.value}",
            reboot_required=prerequisite is self.reboot_after,
        )

    @property
    def touched(self):
        return self.started + self.installed


class ARuntimeThatIsAlreadyGoodTest(unittest.IsolatedAsyncioTestCase):
    """The second run. This is where idempotency is decided."""

    async def asyncSetUp(self):
        self.provisioner = Records()
        self.diagnoses = Diagnoses(diagnosis())
        self.prepared = await RuntimeSetup(
            self.diagnoses, self.provisioner).prepare()

    async def test_it_is_ready(self):
        self.assertIs(self.prepared.progress, Progress.READY)
        self.assertTrue(self.prepared.is_ready)

    async def test_nothing_is_installed(self):
        self.assertEqual(self.provisioner.installed, [])

    async def test_nothing_is_started(self):
        self.assertEqual(self.provisioner.started, [])

    async def test_no_attempt_is_even_recorded(self):
        self.assertEqual(self.prepared.attempts, ())

    async def test_it_costs_exactly_one_look(self):
        # A good machine pays for one diagnosis and nothing else.
        self.assertEqual(self.diagnoses.rounds, 1)


class EachFindingHasExactlyOneResponseTest(unittest.IsolatedAsyncioTestCase):
    async def act_on(self, **findings):
        provisioner = Records()
        await RuntimeSetup(
            Diagnoses(diagnosis(**findings), diagnosis()), provisioner).prepare()
        return provisioner

    async def test_missing_is_installed(self):
        provisioner = await self.act_on(GSHARED_DISTRO=Finding.MISSING)

        self.assertEqual(provisioner.installed, [P.GSHARED_DISTRO])
        self.assertEqual(provisioner.started, [])

    async def test_inactive_is_started_not_installed(self):
        # Installing over something already installed is how a working Docker
        # gets replaced.
        provisioner = await self.act_on(DOCKER_ENGINE=Finding.INACTIVE)

        self.assertEqual(provisioner.started, [P.DOCKER_ENGINE])
        self.assertEqual(provisioner.installed, [])

    async def test_blocked_is_never_acted_on(self):
        # Only the person at the machine can resolve it.
        provisioner = await self.act_on(CONTAINER_GPU=Finding.BLOCKED)

        self.assertEqual(provisioner.touched, [])

    async def test_undetermined_is_never_acted_on(self):
        provisioner = await self.act_on(DOCKER_ENGINE=Finding.UNDETERMINED)

        self.assertEqual(provisioner.touched, [])

    async def test_a_satisfied_prerequisite_is_never_acted_on(self):
        provisioner = await self.act_on(GSHARED_DISTRO=Finding.MISSING)

        self.assertNotIn(P.DOCKER_ENGINE, provisioner.touched)
        self.assertNotIn(P.CONTAINER_TOOLKIT, provisioner.touched)


class NothingIsBelievedWithoutLookingAgainTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_successful_action_is_followed_by_a_fresh_diagnosis(self):
        diagnoses = Diagnoses(diagnosis(DOCKER_ENGINE=Finding.MISSING), diagnosis())

        prepared = await RuntimeSetup(diagnoses, Records()).prepare()

        self.assertTrue(prepared.is_ready)
        self.assertGreaterEqual(diagnoses.rounds, 2)

    async def test_an_action_that_reported_success_but_changed_nothing_is_not_ready(self):
        # The whole reason for looking again. A launcher that returned cleanly
        # is not the same as an engine that answers.
        still_missing = diagnosis(DOCKER_ENGINE=Finding.MISSING)
        provisioner = Records(succeeds=True)

        prepared = await RuntimeSetup(
            Diagnoses(still_missing), provisioner).prepare()

        self.assertFalse(prepared.is_ready)
        self.assertIs(prepared.progress, Progress.STUCK)

    async def test_it_stops_rather_than_repeating_the_same_failure(self):
        provisioner = Records(succeeds=True)

        await RuntimeSetup(
            Diagnoses(diagnosis(DOCKER_ENGINE=Finding.MISSING)), provisioner).prepare()

        # One round of work, then the state had not moved.
        self.assertEqual(provisioner.installed, [P.DOCKER_ENGINE])

    async def test_a_failed_action_never_reports_ready(self):
        prepared = await RuntimeSetup(
            Diagnoses(diagnosis(GSHARED_DISTRO=Finding.MISSING)),
            Records(succeeds=False),
        ).prepare()

        self.assertFalse(prepared.is_ready)

    async def test_what_the_action_said_is_kept(self):
        prepared = await RuntimeSetup(
            Diagnoses(diagnosis(GSHARED_DISTRO=Finding.MISSING)),
            Records(succeeds=False),
        ).prepare()

        attempt = prepared.attempts[0]
        self.assertIs(attempt.action, Action.INSTALL)
        self.assertFalse(attempt.succeeded)
        self.assertIn("GSHARED_DISTRO", attempt.detail)


class ProgressThroughSeveralRoundsTest(unittest.IsolatedAsyncioTestCase):
    async def test_resolving_one_thing_lets_the_next_be_tested(self):
        # A fresh machine: no distribution, then no engine, then ready.
        diagnoses = Diagnoses(
            diagnosis(GSHARED_DISTRO=Finding.MISSING,
                      DOCKER_ENGINE=Finding.UNDETERMINED,
                      CONTAINER_TOOLKIT=Finding.UNDETERMINED,
                      CONTAINER_GPU=Finding.UNDETERMINED),
            diagnosis(DOCKER_ENGINE=Finding.MISSING,
                      CONTAINER_TOOLKIT=Finding.UNDETERMINED,
                      CONTAINER_GPU=Finding.UNDETERMINED),
            diagnosis(CONTAINER_TOOLKIT=Finding.MISSING,
                      CONTAINER_GPU=Finding.UNDETERMINED),
            diagnosis(),
        )
        provisioner = Records()

        prepared = await RuntimeSetup(diagnoses, provisioner).prepare()

        self.assertTrue(prepared.is_ready)
        self.assertEqual(
            provisioner.installed,
            [P.GSHARED_DISTRO, P.DOCKER_ENGINE, P.CONTAINER_TOOLKIT],
        )

    async def test_a_restart_stops_everything_after_it(self):
        # Anything after this would be judged against a half-finished machine.
        provisioner = Records(reboot_after=P.GSHARED_DISTRO)
        diagnoses = Diagnoses(
            diagnosis(GSHARED_DISTRO=Finding.MISSING, DOCKER_ENGINE=Finding.MISSING))

        prepared = await RuntimeSetup(diagnoses, provisioner).prepare()

        self.assertIs(prepared.progress, Progress.NEEDS_A_RESTART)
        self.assertEqual(provisioner.installed, [P.GSHARED_DISTRO])

    async def test_something_only_a_person_can_fix_is_named_as_such(self):
        prepared = await RuntimeSetup(
            Diagnoses(diagnosis(CONTAINER_GPU=Finding.BLOCKED)), Records()).prepare()

        self.assertIs(prepared.progress, Progress.NEEDS_A_PERSON)

    async def test_it_gives_up_rather_than_looping_forever(self):
        never_moves = Diagnoses(diagnosis(DOCKER_ENGINE=Finding.MISSING))
        provisioner = Records()

        prepared = await RuntimeSetup(
            never_moves, provisioner, max_rounds=4).prepare()

        self.assertFalse(prepared.is_ready)
        self.assertLessEqual(len(provisioner.installed), 4)


class WhichActionEachPrerequisiteGetsTest(unittest.IsolatedAsyncioTestCase):
    """The mapping from prerequisite to the one command that answers it."""

    class Distro:
        def __init__(self):
            self.called: list[str] = []

        def _step(self, name):
            self.called.append(name)
            from gshared_worker.wsl_runtime import StepResult
            return StepResult(True, name)

        async def create(self):
            return self._step("create")

        async def configure_boot(self):
            return self._step("configure_boot")

        async def convert_to_wsl2(self):
            return self._step("convert_to_wsl2")

        async def install_docker(self):
            return self._step("install_docker")

        async def start_docker(self):
            return self._step("start_docker")

        async def install_toolkit(self):
            return self._step("install_toolkit")

        async def restart_docker(self):
            return self._step("restart_docker")

    async def called_for(self, method, prerequisite):
        distro = self.Distro()
        await getattr(GsharedProvisioner(distro), method)(prerequisite)
        return distro.called

    async def test_creating_the_distribution_also_gives_it_an_init_system(self):
        # A distribution without one cannot run a service at all.
        self.assertEqual(
            await self.called_for("install", P.GSHARED_DISTRO),
            ["create", "configure_boot"],
        )

    async def test_a_wsl1_distribution_is_converted_not_recreated(self):
        # It exists. Creating it again would throw away whatever is in it.
        self.assertEqual(
            await self.called_for("start", P.GSHARED_DISTRO),
            ["convert_to_wsl2"],
        )

    async def test_nothing_changes_the_windows_wide_default_version(self):
        # Converting our own distribution is ours to do. Deciding what every
        # future distribution the supplier makes lands on is not.
        distro = self.Distro()

        self.assertFalse(hasattr(distro, "use_wsl2_by_default"))

    async def test_a_missing_engine_is_installed(self):
        self.assertEqual(
            await self.called_for("install", P.DOCKER_ENGINE), ["install_docker"])

    async def test_a_stopped_engine_is_started(self):
        self.assertEqual(
            await self.called_for("start", P.DOCKER_ENGINE), ["start_docker"])

    async def test_configuring_the_toolkit_reloads_the_engine(self):
        # Configuring a runtime is only real once the engine has reloaded.
        self.assertEqual(
            await self.called_for("install", P.CONTAINER_TOOLKIT),
            ["install_toolkit", "restart_docker"],
        )

    async def test_a_failed_install_does_not_go_on_to_the_next_step(self):
        class Refuses(self.Distro):
            async def create(self):
                from gshared_worker.wsl_runtime import StepResult
                self.called.append("create")
                return StepResult(False, "no space")

        distro = Refuses()
        result = await GsharedProvisioner(distro).install(P.GSHARED_DISTRO)

        self.assertFalse(result.succeeded)
        self.assertEqual(distro.called, ["create"])

    async def test_there_is_no_action_for_a_gpu_a_container_cannot_see(self):
        # Nothing this program installs fixes it, so there must be no branch
        # here that pretends otherwise.
        result = await GsharedProvisioner(self.Distro()).install(P.CONTAINER_GPU)

        self.assertFalse(result.succeeded)
        self.assertIn("nothing to install", result.detail)


if __name__ == "__main__":
    unittest.main()
