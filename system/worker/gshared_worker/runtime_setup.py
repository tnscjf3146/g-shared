"""Turning a runtime diagnosis into a distribution that can run GPU work.

The supplier runs one program. What WSL and Docker are, and which of them is
missing today, is not theirs to know -- so this acts on what
:mod:`gshared_worker.runtime_diagnosis` found and then **looks again**, rather
than printing instructions or trusting that a command which returned zero
changed anything.

Two rules carry most of the weight:

* **Each finding has exactly one response, and they are not interchangeable.**
  ``MISSING`` installs, ``INACTIVE`` starts. Installing over something already
  installed is how a working Docker gets replaced; starting something that was
  never installed just fails. ``SATISFIED`` is never acted on at all, which is
  what makes a second run cost nothing.
* **Nothing is believed until the diagnosis is run again.** A launcher that
  returned cleanly is not the same as an engine that answers.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from .diagnosis import Finding
from .runtime_diagnosis import RuntimeCheck, RuntimeDiagnosis, RuntimePrerequisite
from .wsl_runtime import WslDistroProvisioner

#: Rounds of act-then-look-again before giving up. A round only repeats when the
#: last one changed something, so this is a backstop against an action that
#: reports success while the machine stays exactly as it was.
MAX_ROUNDS = 4


class Action(Enum):
    #: Present but not running or not configured.
    START = "START"
    #: Absent, and ours to put there.
    INSTALL = "INSTALL"


@dataclass(frozen=True)
class ActionResult:
    succeeded: bool
    detail: str
    #: Windows will not finish this until the machine restarts. Restarting is
    #: never done here: it is the supplier's machine and they may be using it.
    reboot_required: bool = False


@dataclass(frozen=True)
class Attempt:
    prerequisite: RuntimePrerequisite
    action: Action
    succeeded: bool
    detail: str


class Progress(Enum):
    #: Everything checks out; the Worker may go on.
    READY = "READY"
    #: Something only the person at the machine can resolve.
    NEEDS_A_PERSON = "NEEDS_A_PERSON"
    #: Work was done that Windows only finishes on restart.
    NEEDS_A_RESTART = "NEEDS_A_RESTART"
    #: Actions ran and the machine did not move. Repeating would not help.
    STUCK = "STUCK"


@dataclass(frozen=True)
class Setup:
    progress: Progress
    #: What the distribution looked like when this stopped.
    diagnosis: RuntimeDiagnosis
    attempts: tuple[Attempt, ...]

    @property
    def is_ready(self) -> bool:
        return self.progress is Progress.READY


class Provisioner(Protocol):
    """Does the actual work on the distribution."""

    async def start(self, prerequisite: RuntimePrerequisite) -> ActionResult: ...
    async def install(self, prerequisite: RuntimePrerequisite) -> ActionResult: ...


class DiagnosisSource(Protocol):
    async def run(self) -> RuntimeDiagnosis: ...


class GsharedProvisioner:
    """Maps a prerequisite to the one action that answers it.

    Every branch goes through :class:`~gshared_worker.wsl_runtime.WslDistroProvisioner`,
    which names our distribution explicitly. There is no branch here that can
    install, start or alter anything the supplier owns.
    """

    def __init__(self, distro: WslDistroProvisioner | None = None) -> None:
        self._distro = distro or WslDistroProvisioner()

    async def start(self, prerequisite: RuntimePrerequisite) -> ActionResult:
        if prerequisite is RuntimePrerequisite.GSHARED_DISTRO:
            # Registered on WSL1. Our own distribution is converted by name --
            # not the Windows-wide default, which would change what every
            # future distribution the supplier makes lands on.
            return _as_action(await self._distro.convert_to_wsl2())
        if prerequisite is RuntimePrerequisite.DOCKER_ENGINE:
            return _as_action(await self._distro.start_docker())
        return ActionResult(
            False, f"There is nothing to start for {prerequisite.value}.")

    async def install(self, prerequisite: RuntimePrerequisite) -> ActionResult:
        if prerequisite is RuntimePrerequisite.GSHARED_DISTRO:
            created = await self._distro.create()
            if not created.succeeded:
                return _as_action(created)
            # A distribution without an init system cannot run a service, and
            # the setting only takes effect once it has been restarted.
            return _as_action(await self._distro.configure_boot())
        if prerequisite is RuntimePrerequisite.DOCKER_ENGINE:
            return _as_action(await self._distro.install_docker())
        if prerequisite is RuntimePrerequisite.CONTAINER_TOOLKIT:
            installed = await self._distro.install_toolkit()
            if not installed.succeeded:
                return _as_action(installed)
            # Configuring a runtime is only real once the engine has reloaded.
            return _as_action(await self._distro.restart_docker())
        return ActionResult(
            False, f"There is nothing to install for {prerequisite.value}.")


class RuntimeSetup:
    """Act on what is missing, look again, and decide when to stop."""

    def __init__(
        self,
        diagnosis: DiagnosisSource,
        provisioner: Provisioner,
        *,
        max_rounds: int = MAX_ROUNDS,
        announce: Any = None,
    ) -> None:
        self._diagnosis = diagnosis
        self._provisioner = provisioner
        self._max_rounds = max_rounds
        #: Told about each action as it starts. Preparing a fresh machine pulls
        #: down well over a gigabyte, and silence for that long is what a person
        #: reads as the program having hung.
        self._announce = announce

    async def prepare(self) -> Setup:
        attempts: list[Attempt] = []
        diagnosis = await self._diagnosis.run()
        previously_outstanding: frozenset[RuntimePrerequisite] | None = None

        for _ in range(self._max_rounds):
            if diagnosis.is_ready:
                # Nothing was installed, nothing was started, nothing was
                # replaced. This is the whole of a second run on a good machine.
                return Setup(Progress.READY, diagnosis, tuple(attempts))

            outstanding = _outstanding(diagnosis)
            if previously_outstanding is not None and outstanding == previously_outstanding:
                # A whole round of work left the distribution in the same state.
                # Trying the same actions again would only take longer to fail.
                return Setup(_stalled_on(diagnosis), diagnosis, tuple(attempts))
            previously_outstanding = outstanding

            if not diagnosis.resolvable:
                # Nothing left that this program may touch. Whatever remains is
                # either the person's to do or could not be tested because of it.
                return Setup(_stalled_on(diagnosis), diagnosis, tuple(attempts))

            for check in diagnosis.resolvable:
                if self._announce is not None:
                    self._announce(check)
                result = await self._act_on(check)
                attempts.append(Attempt(
                    check.prerequisite, _action_for(check),
                    result.succeeded, result.detail,
                ))
                if result.reboot_required:
                    # Anything after this would be judged against a half-finished
                    # machine, so stop and let the restart happen first.
                    return Setup(
                        Progress.NEEDS_A_RESTART,
                        await self._diagnosis.run(),
                        tuple(attempts),
                    )

            # Look again rather than believe the actions.
            diagnosis = await self._diagnosis.run()

        return Setup(_stalled_on(diagnosis), diagnosis, tuple(attempts))

    async def _act_on(self, check: RuntimeCheck) -> ActionResult:
        if _action_for(check) is Action.START:
            return await self._provisioner.start(check.prerequisite)
        return await self._provisioner.install(check.prerequisite)


def _as_action(step) -> ActionResult:
    return ActionResult(
        step.succeeded, step.detail,
        reboot_required=getattr(step, "restart_required", False))


def _action_for(check: RuntimeCheck) -> Action:
    if check.finding is Finding.INACTIVE:
        return Action.START
    if check.finding is Finding.MISSING:
        return Action.INSTALL
    raise ValueError(f"{check.finding} is not something to act on")


def _stalled_on(diagnosis: RuntimeDiagnosis) -> Progress:
    if diagnosis.is_ready:
        return Progress.READY
    if diagnosis.blocked:
        return Progress.NEEDS_A_PERSON
    return Progress.STUCK


def _outstanding(diagnosis: RuntimeDiagnosis) -> frozenset[RuntimePrerequisite]:
    """Everything not yet satisfied, however it fails.

    Untested prerequisites count. Resolving what they wait on is exactly the
    progress worth continuing for, and leaving them out would read a
    distribution that just gained Docker as one that had not moved.
    """
    return frozenset(
        check.prerequisite for check in diagnosis.checks if not check.is_resolved
    )
