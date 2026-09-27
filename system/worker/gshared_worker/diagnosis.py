"""What is missing before this machine can run GPU work, and what would fix it.

Every prerequisite is judged on its own, so the thing that is actually missing
can be named rather than whatever happened to be checked first.

The rule that matters most is that a prerequisite is only reported as failing
when it was really tested. WSL cannot run on a machine whose firmware forbids
virtualization, so on such a machine the WSL check would fail too -- and
whoever read the report would go after WSL while the real gap sat untouched.
Anything resting on an unresolved prerequisite is reported as
:data:`Finding.UNDETERMINED` instead, which says "not tested", not "broken".

Nothing here installs or changes anything. It only looks.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .config import WSL_DISTRO_NAME

#: Windows 10 21H2. Below this the GPU cannot be projected into WSL2 at all, so
#: no amount of installing helps.
MINIMUM_WINDOWS_BUILD = 19044


class Prerequisite(Enum):
    """Something this machine needs before it can take GPU work.

    Only what the Worker can ask today. The distribution, its Docker Engine and
    the container toolkit are judged by the code that knows how to reach inside
    the distribution, and are added here with it.
    """

    WINDOWS_VERSION = "WINDOWS_VERSION"
    HARDWARE_VIRTUALIZATION = "HARDWARE_VIRTUALIZATION"
    NVIDIA_DRIVER = "NVIDIA_DRIVER"
    WSL2 = "WSL2"


class Finding(Enum):
    """What was found, phrased by what can be done about it."""

    SATISFIED = "SATISFIED"

    #: Absent, and putting it there is something a program could do.
    MISSING = "MISSING"

    #: Present but not running or not configured; starting it is ours to do.
    INACTIVE = "INACTIVE"

    #: Only the person at the machine can resolve it -- firmware, hardware, a
    #: Windows update, or a driver whose installation is not ours to automate.
    BLOCKED = "BLOCKED"

    #: Not tested, because something it rests on is unresolved, or because the
    #: machine would not answer. Emphatically not a failure: treating it as one
    #: sends somebody after the wrong thing.
    UNDETERMINED = "UNDETERMINED"


@dataclass(frozen=True)
class Check:
    prerequisite: Prerequisite
    finding: Finding
    #: What was observed, in the machine's own terms.
    detail: str
    #: What would resolve it. None when there is nothing to say yet.
    remedy: str | None = None

    @property
    def is_resolved(self) -> bool:
        return self.finding is Finding.SATISFIED


@dataclass(frozen=True)
class Diagnosis:
    checks: tuple[Check, ...]

    @property
    def is_ready(self) -> bool:
        return all(check.is_resolved for check in self.checks)

    @property
    def blocked(self) -> tuple[Check, ...]:
        """What the person has to deal with before anything else can proceed."""
        return tuple(c for c in self.checks if c.finding is Finding.BLOCKED)

    @property
    def resolvable(self) -> tuple[Check, ...]:
        """What a program could put right, in the order it must be done."""
        return tuple(
            c for c in self.checks
            if c.finding in (Finding.MISSING, Finding.INACTIVE)
        )

    @property
    def undetermined(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.finding is Finding.UNDETERMINED)

    def find(self, prerequisite: Prerequisite) -> Check:
        for check in self.checks:
            if check.prerequisite is prerequisite:
                return check
        raise KeyError(prerequisite)


class HostProbe(Protocol):
    """Reads the machine. Every method reports absence rather than raising."""

    async def windows_build(self) -> int | None: ...
    async def firmware_virtualization_enabled(self) -> bool | None: ...
    async def nvidia_driver(self) -> object | None: ...
    async def wsl_default_version(self) -> int | None: ...
    async def wsl_can_create_named_distributions(self) -> bool | None: ...


class EnvironmentDiagnosis:
    """Judges every prerequisite, and knows which ones rest on which."""

    def __init__(self, host: HostProbe) -> None:
        self._host = host

    async def run(self) -> Diagnosis:
        found: dict[Prerequisite, Check] = {}
        #: Prerequisites never tested because something they rest on was
        #: unresolved. Kept apart from the ones that were tested and simply
        #: could not be read: those two share a finding but not a meaning.
        skipped: set[Prerequisite] = set()

        def record(check: Check) -> Check:
            found[check.prerequisite] = check
            return check

        def skip(prerequisite: Prerequisite, blocker: Prerequisite) -> Check:
            skipped.add(prerequisite)
            return Check(
                prerequisite, Finding.UNDETERMINED,
                f"확인하지 않음: {blocker.value} 문제가 먼저 해결되어야 합니다.",
            )

        def unresolved(*needed: Prerequisite) -> Prerequisite | None:
            """The first prerequisite known not to hold, or never tested at all.

            A prerequisite that was merely unreadable does not count: the direct
            test after it is better evidence than the indirect one, so it is
            still worth running. But one that was skipped because its own
            dependency was unmet says nothing either way.
            """
            for prerequisite in needed:
                if prerequisite in skipped:
                    return prerequisite
                check = found.get(prerequisite)
                if check is not None and check.finding in (
                    Finding.MISSING, Finding.INACTIVE, Finding.BLOCKED
                ):
                    return prerequisite
            return None

        record(await self._windows())
        record(await self._virtualization())
        record(await self._driver())
        record(await self._wsl(skip, unresolved(
            Prerequisite.WINDOWS_VERSION, Prerequisite.HARDWARE_VIRTUALIZATION
        )))

        return Diagnosis(tuple(found[p] for p in Prerequisite))

    async def _windows(self) -> Check:
        build = await self._host.windows_build()
        if build is None:
            return Check(
                Prerequisite.WINDOWS_VERSION, Finding.BLOCKED,
                "Windows가 아닙니다.",
                "Worker는 Windows의 WSL2 안에서 GPU 컨테이너를 실행합니다.",
            )
        if build < MINIMUM_WINDOWS_BUILD:
            return Check(
                Prerequisite.WINDOWS_VERSION, Finding.BLOCKED,
                f"Windows 빌드 {build}은(는) 최소 요구 빌드 {MINIMUM_WINDOWS_BUILD}보다 낮습니다.",
                "Windows를 업데이트하세요. 이 빌드보다 낮으면 WSL2에서 GPU를 쓸 수 없습니다.",
            )
        return Check(
            Prerequisite.WINDOWS_VERSION, Finding.SATISFIED, f"Windows 빌드 {build}."
        )

    async def _virtualization(self) -> Check:
        enabled = await self._host.firmware_virtualization_enabled()
        if enabled is False:
            return Check(
                Prerequisite.HARDWARE_VIRTUALIZATION, Finding.BLOCKED,
                "펌웨어에서 가상화가 꺼져 있습니다.",
                "BIOS 또는 UEFI 설정에서 가상화(VT-x / AMD-V)를 켜세요. Windows에 설치하는 프로그램으로는 켤 수 없습니다.",
            )
        if enabled is None:
            return Check(
                Prerequisite.HARDWARE_VIRTUALIZATION, Finding.UNDETERMINED,
                "펌웨어 설정을 읽지 못했습니다.",
            )
        # Not "enabled in firmware": on a machine already running a hypervisor
        # the firmware flag reads False and the probe concludes yes from the
        # hypervisor instead. Saying otherwise would print something the machine
        # itself contradicts.
        return Check(
            Prerequisite.HARDWARE_VIRTUALIZATION, Finding.SATISFIED,
            "이 PC는 가상화를 사용할 수 있습니다.",
        )

    async def _driver(self) -> Check:
        driver = await self._host.nvidia_driver()
        if driver is None:
            return Check(
                Prerequisite.NVIDIA_DRIVER, Finding.BLOCKED,
                "사용할 수 있는 NVIDIA 드라이버를 찾지 못했습니다.",
                "이 GPU에 맞는 NVIDIA 드라이버를 설치하세요. 드라이버 설치는 재부팅이 필요하고 그래픽카드마다 달라서 자동으로 하지 않습니다.",
            )
        gpu_name = getattr(driver, "gpu_name", None)
        card = f" ({gpu_name})" if gpu_name else ""
        return Check(
            Prerequisite.NVIDIA_DRIVER, Finding.SATISFIED,
            f"NVIDIA 드라이버 {driver.version}{card}.",
        )

    async def _wsl(self, skip, blocker: Prerequisite | None) -> Check:
        if blocker is not None:
            return skip(Prerequisite.WSL2, blocker)
        version = await self._host.wsl_default_version()
        if version is None:
            # Not MISSING: that would promise something installs it, and
            # "wsl --install" needs an administrator and a restart -- neither of
            # which is taken on a supplier's behalf. Saying so plainly beats
            # claiming a fix nothing here can carry out.
            return Check(
                Prerequisite.WSL2, Finding.BLOCKED,
                "WSL이 설치되어 있지 않습니다.",
                "관리자 권한 PowerShell에서  wsl --install  을 실행한 뒤 PC를 다시 시작하세요. WSL은 다시 시작한 후에 동작합니다.",
            )
        if version < 2:
            return Check(
                Prerequisite.WSL2, Finding.INACTIVE,
                f"WSL은 설치되어 있지만 기본 버전이 {version}입니다.",
                "WSL 기본 버전을 2로 바꾸세요. 버전 1에서는 GPU를 쓸 수 없습니다.",
            )
        # The Worker's distribution is created by name and in a location of our
        # choosing. An older wsl.exe rejects both options, and the run then ends
        # on a message about a command-line flag -- which reads as a fault here
        # rather than one command a supplier can run. None means the question
        # went unanswered, which is not a reason to stop a capable machine.
        if await self._host.wsl_can_create_named_distributions() is False:
            return Check(
                Prerequisite.WSL2, Finding.BLOCKED,
                "이 WSL 버전으로는 전용 배포판을 만들 수 없습니다.",
                f"관리자 권한 PowerShell에서  wsl --update  를 실행해 WSL을 업데이트한 뒤 G-shared를 다시 실행하세요. {WSL_DISTRO_NAME} 배포판은 이전 wsl.exe가 지원하지 않는 옵션으로 만들어집니다.",
            )
        return Check(
            Prerequisite.WSL2, Finding.SATISFIED, "WSL 기본 버전이 2입니다."
        )
