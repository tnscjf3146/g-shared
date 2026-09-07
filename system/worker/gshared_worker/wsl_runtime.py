"""A WSL2 distribution of our own, with its own Docker Engine.

Docker Desktop is not used and not consulted. Everything runs inside a
distribution this program creates and owns, so the supplier's own Ubuntu, their
own Docker and their own containers are never touched -- and neither is the
Windows Docker socket layer, which is where a broken Docker Desktop takes the
whole machine down with it.

The keystone is :class:`WslCommandRunner`. It satisfies the same
:class:`~gshared_worker.command_runner.CommandRunner` protocol the Windows probe
uses, so a caller that builds a flat ``["docker", ...]`` command list runs it
inside the distribution without knowing that is what happened. There is no path
by which any command here reaches ``docker.exe`` on Windows: what it builds
always starts with ``wsl``.

**Every method names the distribution explicitly.** Nothing in this file can act
on a distribution the supplier installed for themselves.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from .command_runner import AsyncSubprocessRunner, CommandResult, CommandRunner
from .config import WSL_DISTRO_NAME
from .errors import WorkerError

#: The base image the distribution is built from. An LTS release, because the
#: Docker and NVIDIA package repositories both target those.
BASE_DISTRO = "Ubuntu-24.04"

#: Which base image to build on, best first.
#:
#: What a machine is offered depends on its WSL: an older one has never heard of
#: the newest release and refuses to install it, so a single hard-coded name
#: meets WSL_E_DISTRO_NOT_FOUND seconds in. Every entry is Ubuntu because
#: everything installed afterwards comes from Ubuntu's own archive.
BASE_DISTRO_PREFERENCE = ("Ubuntu-24.04", "Ubuntu-22.04", "Ubuntu")

#: What wsl.exe says when it has never heard of the name it was given.
_NOT_FOUND = "WSL_E_DISTRO_NOT_FOUND"

#: The column heading wsl.exe prints above the installable names.
_HEADING = "NAME"

#: Everything installed into the distribution afterwards is apt from Ubuntu's
#: own archive, so the base has to be one.
_UBUNTU = "Ubuntu"

#: Creating a distribution downloads a base image; installing Docker and the
#: toolkit downloads packages. Both are minutes, not seconds.
CREATE_TIMEOUT_SECONDS = 1800
INSTALL_TIMEOUT_SECONDS = 1800
#: Converting rewrites the distribution's disk.
CONVERT_TIMEOUT_SECONDS = 1800
#: Asking a question of WSL or of the distribution.
COMMAND_TIMEOUT_SECONDS = 120
OUTPUT_LIMIT = 1024 * 1024


@dataclass(frozen=True)
class DistroState:
    exists: bool
    #: WSL version, or None when the distribution is not registered.
    version: int | None = None
    running: bool = False

    @property
    def is_usable(self) -> bool:
        """Registered and on WSL2. Version 1 cannot reach the GPU at all."""
        return self.exists and self.version == 2


@dataclass(frozen=True)
class StepResult:
    succeeded: bool
    detail: str
    #: Windows will not finish this until the machine restarts. Restarting is
    #: never done here: it is the supplier's machine and they may be using it.
    restart_required: bool = False


class WslCommandRunner:
    """Runs a command inside one WSL distribution, never on Windows."""

    def __init__(
        self,
        runner: CommandRunner | None = None,
        *,
        distro: str = WSL_DISTRO_NAME,
        user: str = "root",
    ) -> None:
        self._runner = runner or AsyncSubprocessRunner()
        self._distro = distro
        self._user = user

    @property
    def prefix(self) -> list[str]:
        """How every command reaches the distribution.

        ``--exec``, not ``--``. Both stop wsl.exe from reading the child's flags
        as its own, but ``--`` additionally runs the command through the
        distribution's default shell, which expands the arguments once before
        the real command sees them. A ``$VERSION_CODENAME`` is then replaced
        with nothing by that outer shell -- which is how the Docker apt entry
        comes out with an empty release name and breaks every later apt-get.
        """
        return ["wsl", "-d", self._distro, "-u", self._user, "--exec"]

    async def run(
        self, args: list[str], *, timeout: float, max_output_bytes: int
    ) -> CommandResult:
        result = await self._runner.run(
            self.prefix + args, timeout=timeout, max_output_bytes=max_output_bytes
        )
        # wsl.exe writes its own messages as UTF-16, which arrive here riddled
        # with NULs once decoded as UTF-8. The child's output is unaffected, so
        # this only ever tidies up wsl's own errors.
        return CommandResult(
            result.exit_code,
            result.stdout.replace("\x00", ""),
            result.stderr.replace("\x00", ""),
        )


class WslDistroProbe:
    """Reads what WSL knows about our distribution, and only ours."""

    def __init__(
        self, runner: CommandRunner | None = None, *, distro: str = WSL_DISTRO_NAME
    ) -> None:
        self._runner = runner or AsyncSubprocessRunner()
        self._distro = distro

    async def state(self) -> DistroState:
        result = await _quietly(
            self._runner, ["wsl", "--list", "--verbose"], COMMAND_TIMEOUT_SECONDS
        )
        if result is None:
            return DistroState(exists=False)
        for line in result.stdout.replace("\x00", "").splitlines():
            entry = _parse_distro_line(line)
            if entry is None:
                continue
            name, running, version = entry
            if name == self._distro:
                return DistroState(exists=True, version=version, running=running)
        return DistroState(exists=False)


def _parse_distro_line(line: str) -> tuple[str, bool, int | None] | None:
    """One row of ``wsl --list --verbose``, or None for a header or blank."""
    # The default distribution is marked with a leading asterisk, which is not
    # part of its name.
    stripped = line.strip().lstrip("*").strip()
    if not stripped:
        return None
    columns = re.split(r"\s{2,}|\t+", stripped)
    if len(columns) < 2:
        columns = stripped.split()
    if len(columns) < 2 or columns[0].upper() == _HEADING:
        return None
    name = columns[0]
    running = columns[1].strip().casefold() == "running"
    version: int | None = None
    if len(columns) >= 3 and columns[2].strip().isdigit():
        version = int(columns[2].strip())
    return name, running, version


#: Written into the distribution so that services can be managed normally.
#: Without it Ubuntu on WSL has no init and ``service docker start`` has nothing
#: to talk to.
WSL_CONF = """[boot]
systemd=true

[interop]
appendWindowsPath=false
"""

#: Installs the Docker Engine from Docker's own repository, not Ubuntu's, so
#: that the NVIDIA container toolkit added in the next step has the version it
#: documents. Written to be safe to run again: a distribution that already has
#: Docker is left alone.
INSTALL_DOCKER = r"""
set -eu
if command -v docker >/dev/null 2>&1; then
  echo "gshared: docker already present"
  exit 0
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -qq
apt-get install -y -qq docker-ce docker-ce-cli containerd.io
echo "gshared: docker installed"
"""

#: Starts the engine and waits for it to answer. Tries systemd first because
#: that is what the distribution is configured for, and falls back to running
#: the daemon directly so a distribution whose systemd did not come up is still
#: usable.
START_DOCKER = r"""
set -u
if docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
  echo "gshared: engine already answering"
  exit 0
fi
if command -v systemctl >/dev/null 2>&1 && systemctl is-system-running --quiet 2>/dev/null; then
  systemctl enable --now docker >/dev/null 2>&1 || true
else
  service docker start >/dev/null 2>&1 || true
fi
for _ in $(seq 1 30); do
  if docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
    echo "gshared: engine answered"
    exit 0
  fi
  sleep 2
done
if ! pgrep -x dockerd >/dev/null 2>&1; then
  nohup dockerd >/var/log/gshared-dockerd.log 2>&1 &
  for _ in $(seq 1 30); do
    if docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
      echo "gshared: engine answered after starting dockerd directly"
      exit 0
    fi
    sleep 2
  done
fi
echo "gshared: engine did not answer" >&2
exit 1
"""

#: Installs NVIDIA's container toolkit and points Docker at it. The Windows
#: driver already projects the GPU into WSL through /dev/dxg; this is what lets
#: a *container* reach it. Safe to run again: an already-configured runtime is
#: left alone.
INSTALL_TOOLKIT = r"""
set -eu
if command -v nvidia-ctk >/dev/null 2>&1 \
   && grep -q '"nvidia"' /etc/docker/daemon.json 2>/dev/null; then
  echo "gshared: toolkit already configured"
  exit 0
fi
export DEBIAN_FRONTEND=noninteractive
if ! command -v nvidia-ctk >/dev/null 2>&1; then
  apt-get install -y -qq ca-certificates curl gnupg
  install -m 0755 -d /usr/share/keyrings
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | gpg --yes --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    > /etc/apt/sources.list.d/nvidia-container-toolkit.list
  apt-get update -qq
  apt-get install -y -qq nvidia-container-toolkit
fi
nvidia-ctk runtime configure --runtime=docker
echo "gshared: toolkit installed and docker configured"
"""

#: Restarts the engine so a newly configured runtime is actually loaded, then
#: waits for it to answer again rather than assuming it came back.
RESTART_DOCKER = r"""
set -u
if command -v systemctl >/dev/null 2>&1 && systemctl is-system-running --quiet 2>/dev/null; then
  systemctl restart docker >/dev/null 2>&1 || true
else
  service docker restart >/dev/null 2>&1 || true
fi
for _ in $(seq 1 30); do
  if docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
    echo "gshared: engine answered after restart"
    exit 0
  fi
  sleep 2
done
echo "gshared: engine did not come back after restart" >&2
exit 1
"""

#: Reports what is inside the distribution without changing any of it. Written
#: as one script so a probe is one crossing into the distribution rather than
#: four, each of which costs a WSL round trip.
_RUNTIME_STATE = r"""
if docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
  echo engine=running
elif command -v docker >/dev/null 2>&1; then
  echo engine=installed
else
  echo engine=absent
fi
if command -v nvidia-ctk >/dev/null 2>&1 \
   && docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q nvidia; then
  echo toolkit=configured
elif command -v nvidia-ctk >/dev/null 2>&1 \
     && grep -q nvidia /etc/docker/daemon.json 2>/dev/null; then
  echo toolkit=configured
else
  echo toolkit=absent
fi
"""


class GsharedRuntimeProbe:
    """Reads the inside of the distribution, and changes nothing.

    ``engine_state`` is deliberately not "does docker exist": a ``docker`` on
    PATH whose daemon is dead answers ``command -v`` and nothing else, so the
    script asks the daemon for its version before calling the engine running.
    """

    def __init__(self, inside: CommandRunner | None = None) -> None:
        self._inside = inside or WslCommandRunner()

    async def _read(self) -> dict[str, str]:
        try:
            result = await self._inside.run(
                ["bash", "-lc", _RUNTIME_STATE],
                timeout=COMMAND_TIMEOUT_SECONDS,
                max_output_bytes=OUTPUT_LIMIT,
            )
        except (FileNotFoundError, WorkerError):
            return {}
        answers: dict[str, str] = {}
        for line in result.stdout.replace("\x00", "").splitlines():
            key, _, value = line.strip().partition("=")
            if value:
                answers[key] = value
        return answers

    async def engine_state(self) -> str:
        """One of ``running``, ``installed``, ``absent``.

        Absent is the safe reading of a distribution that would not answer: it
        leads to an install, which is idempotent, rather than to a false
        all-clear.
        """
        return (await self._read()).get("engine", "absent")

    async def toolkit_configured(self) -> bool:
        return (await self._read()).get("toolkit") == "configured"


class WslDistroProvisioner:
    """Creates our distribution and puts a Docker Engine in it."""

    def __init__(
        self,
        runner: CommandRunner | None = None,
        *,
        distro: str = WSL_DISTRO_NAME,
        base_distro: str = BASE_DISTRO,
        location: str | None = None,
        inside: CommandRunner | None = None,
    ) -> None:
        self._runner = runner or AsyncSubprocessRunner()
        self._distro = distro
        self._base_distro = base_distro
        self._location = location or default_location()
        self._inside = inside or WslCommandRunner(self._runner, distro=distro)

    async def create(self) -> StepResult:
        """Register the distribution, without starting a shell in it.

        ``--no-launch`` matters: the first launch of an Ubuntu image otherwise
        prompts for a username and password, which nobody is there to answer.

        Each name this machine might accept is tried in turn. Which releases a
        WSL offers depends on that WSL, and one it has never heard of is refused
        in seconds -- so stopping at the first refusal gives up with names left
        untried. Any other failure stops immediately: a full disk is not fixed
        by asking for a different release, and each attempt may take half an
        hour.
        """
        os.makedirs(self._location, exist_ok=True)
        offered = await self._offered_base_distros()
        refused: list[str] = []

        for base in self._candidates(offered):
            result = await self._run([
                "wsl", "--install", base,
                "--name", self._distro,
                "--location", self._location,
                "--no-launch",
                "--web-download",
            ], timeout=CREATE_TIMEOUT_SECONDS)
            if result is None:
                return StepResult(False, "wsl.exe could not be run")
            said = _message(result) or "the distribution could not be created"
            if result.exit_code == 0:
                # Asked for, not assumed. wsl.exe has been seen reporting
                # success and registering nothing, after which the next step
                # runs a command inside a distribution that is not there and
                # fails complaining about the wrong thing at the wrong step.
                if await self._is_registered():
                    return StepResult(True, f"{self._distro} was created from {base}")
                # The signature of a WSL that Windows has installed but not
                # finished: it answers every question and registers nothing.
                return StepResult(False, (
                    f"wsl.exe reported success but {self._distro} was not "
                    "registered afterwards. Windows usually finishes installing "
                    "WSL only after a restart -- restart this PC and run "
                    f"G-shared again. wsl.exe said: {said}"
                ), restart_required=True)
            if _NOT_FOUND not in said:
                return StepResult(False, said)
            refused.append(base)

        return StepResult(False, _nothing_worked(refused, offered))

    async def _is_registered(self) -> bool:
        """Whether our distribution is actually there now."""
        result = await self._run(
            ["wsl", "--list", "--quiet"], timeout=COMMAND_TIMEOUT_SECONDS
        )
        if result is None or result.exit_code != 0:
            return False
        listed = {line.strip() for line in result.stdout.splitlines() if line.strip()}
        return self._distro in listed

    def _candidates(self, offered: frozenset[str]) -> list[str]:
        """Every name worth asking for, best first."""
        ordered = [name for name in BASE_DISTRO_PREFERENCE if name in offered]
        # The preference list names the releases worth having, not the only ones
        # that work: everything installed afterwards comes from Ubuntu's archive
        # and an older Ubuntu serves that. Another family would not -- it would
        # fail later and much further from the cause.
        ordered += sorted(
            (name for name in offered
             if name.startswith(_UBUNTU) and name not in ordered),
            reverse=True)
        for name in (*BASE_DISTRO_PREFERENCE, self._base_distro):
            if name not in ordered:
                ordered.append(name)
        return ordered

    async def _offered_base_distros(self) -> frozenset[str]:
        """The installable names wsl.exe lists, empty when it would not say."""
        result = await self._run(
            ["wsl", "--list", "--online"], timeout=COMMAND_TIMEOUT_SECONDS
        )
        if result is None or result.exit_code != 0:
            return frozenset()
        names = set()
        for line in result.stdout.splitlines():
            # Only the left column is installable; the friendly name beside it
            # ("Ubuntu 24.04 LTS") is not a name wsl.exe accepts.
            parts = line.split()
            if parts and parts[0] != _HEADING:
                names.add(parts[0])
        return frozenset(names)

    async def convert_to_wsl2(self) -> StepResult:
        """Convert our own distribution to WSL2. Only ours, and by name.

        Deliberately not ``wsl --set-default-version 2``: that changes what
        *future* distributions Windows creates default to -- a global setting
        that outlives G-shared and touches distributions the supplier has not
        made yet -- and it does not convert anything that already exists. What
        is wrong here is one registered distribution's version, so that is what
        is named and that is all that changes.

        Converting rewrites the distribution's disk, so it takes minutes rather
        than seconds.
        """
        result = await self._run(
            ["wsl", "--set-version", self._distro, "2"],
            timeout=CONVERT_TIMEOUT_SECONDS,
        )
        if result is None:
            return StepResult(False, "wsl.exe could not be run")
        if result.exit_code != 0:
            return StepResult(
                False,
                _message(result) or f"{self._distro} could not be converted to WSL2",
            )

        # Asked for, not assumed. wsl.exe has been seen reporting success on a
        # conversion that did not take, and a distribution still on version 1
        # has no GPU at all -- believing the exit code here would hand the
        # Worker a runtime that cannot do the one thing it exists for.
        version = await self._registered_version()
        if version != 2:
            return StepResult(False, (
                f"wsl.exe reported success but {self._distro} is still "
                f"registered as WSL version {version if version else 'unknown'}. "
                f"{_message(result)}".strip()
            ))
        return StepResult(True, f"{self._distro} was converted to WSL2")

    async def _registered_version(self) -> int | None:
        """What version WSL says our distribution is on, read back fresh."""
        result = await self._run(
            ["wsl", "--list", "--verbose"], timeout=COMMAND_TIMEOUT_SECONDS
        )
        if result is None:
            return None
        for line in result.stdout.splitlines():
            entry = _parse_distro_line(line)
            if entry is not None and entry[0] == self._distro:
                return entry[2]
        return None

    async def configure_boot(self) -> StepResult:
        """Give the distribution an init system, then restart it to take effect."""
        written = await self._inside.run(
            ["sh", "-c", f"cat > /etc/wsl.conf <<'EOF'\n{WSL_CONF}EOF"],
            timeout=COMMAND_TIMEOUT_SECONDS,
            max_output_bytes=OUTPUT_LIMIT,
        )
        if written.exit_code != 0:
            return StepResult(
                False, _message(written) or "/etc/wsl.conf could not be written")

        terminated = await self._run(
            ["wsl", "--terminate", self._distro], timeout=COMMAND_TIMEOUT_SECONDS
        )
        if terminated is None or terminated.exit_code != 0:
            return StepResult(False, "the distribution could not be restarted")
        return StepResult(True, "systemd enabled and the distribution restarted")

    async def install_docker(self) -> StepResult:
        return await self._inside_script(INSTALL_DOCKER, "Docker could not be installed")

    async def start_docker(self) -> StepResult:
        return await self._inside_script(START_DOCKER, "the engine did not start")

    async def install_toolkit(self) -> StepResult:
        return await self._inside_script(
            INSTALL_TOOLKIT, "the container toolkit could not be installed")

    async def restart_docker(self) -> StepResult:
        """Reload the engine so a newly configured runtime is picked up."""
        return await self._inside_script(RESTART_DOCKER, "the engine did not come back")

    async def _inside_script(self, script: str, when_it_fails: str) -> StepResult:
        result = await self._inside.run(
            ["bash", "-lc", script],
            timeout=INSTALL_TIMEOUT_SECONDS,
            max_output_bytes=OUTPUT_LIMIT,
        )
        if result.exit_code != 0:
            return StepResult(False, _message(result) or when_it_fails)
        return StepResult(True, _last_line(result.stdout) or when_it_fails)

    async def _run(self, args: list[str], *, timeout: float) -> CommandResult | None:
        result = await _quietly(self._runner, args, timeout)
        if result is None:
            return None
        return CommandResult(
            result.exit_code,
            result.stdout.replace("\x00", ""),
            result.stderr.replace("\x00", ""),
        )


async def _quietly(
    runner: CommandRunner, args: list[str], timeout: float
) -> CommandResult | None:
    """Run a question, reporting an unanswerable one as None.

    ``CommandError`` inherits ``WorkerError``, so the one ``except`` here covers
    a missing wsl.exe, a timeout and a flood of output alike -- all of which
    mean the same thing to a caller: this machine did not answer.
    """
    try:
        return await runner.run(args, timeout=timeout, max_output_bytes=OUTPUT_LIMIT)
    except FileNotFoundError:
        return None
    except WorkerError:
        return None


def default_location() -> str:
    """Where the distribution's disk lives. Under the user's own profile."""
    root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(root, "G-shared", "wsl", WSL_DISTRO_NAME)


def _nothing_worked(refused: list[str], offered: frozenset[str]) -> str:
    """Why no name worked, in enough detail to act on without asking again."""
    tried = ", ".join(refused) or "nothing"
    if not offered:
        return (
            f"WSL does not have any of these to install: {tried}. Its list of "
            "available distributions could not be read either, so check the "
            "network and run  wsl --update  as administrator."
        )
    return (
        f"WSL does not have any of these to install: {tried}. "
        f"It offers: {', '.join(sorted(offered))}."
    )


def _message(result: CommandResult) -> str:
    """Everything the tool said, not only the line with the code in it.

    wsl.exe writes the explanation first and the error code last, so keeping
    just the last line throws away "the distribution name is invalid, run
    wsl.exe --list --online" and keeps the part nobody can act on.
    """
    return _all_lines(result.stderr) or _all_lines(result.stdout)


def _all_lines(text: str) -> str:
    return " ".join(line.strip() for line in text.splitlines() if line.strip())


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""
