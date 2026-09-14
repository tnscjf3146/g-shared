"""The escrow contract's interface, read from the Blockchain team's artifact.

There is exactly one source of truth for this and it is not in this package:
``blockchain/abi/GPUJobEscrow.json``, generated from the compiler output by
``blockchain/scripts/export-abi.mjs``. Nothing here retypes a signature.

**Why this file exists at all.** The previous version of the escrow client
carried a hand-written ABI, derived by reading Solidity on an unmerged branch.
Every one of its assumptions turned out to be wrong when the contract was
redesigned -- ``FUNDED`` was 2 and became 1, the job struct grew a field in
the middle, and the ownership check was against the wrong address entirely.
None of that failed loudly. A hand-copied ABI decodes the wrong struct into
plausible numbers and hands them to code that is about to sign a transaction.

So the ABI is loaded, and :func:`check_contract_matches` compares the
contract's own ``stateEnum`` against the enum this package uses. If the
Blockchain team renumbers a state again, the tests fail here rather than a
Worker accepting a job that was never funded.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping

#: Where the artifact lives, relative to the repository root.
ARTIFACT_RELATIVE_PATH = Path("blockchain") / "abi" / "GPUJobEscrow.json"

#: The commit the ABI in this repository was generated from. Recorded so a
#: report can name it; not enforced, because the Blockchain team regenerates
#: the artifact and this constant would go stale on their schedule, not ours.
EXPECTED_CONTRACT_NAME = "GPUJobEscrow"


class AbiUnavailable(RuntimeError):
    """The canonical ABI could not be read. Nothing on chain can be done."""


class AbiMismatch(RuntimeError):
    """The ABI does not describe the contract this package was written for."""


def default_artifact_path() -> Path:
    """The artifact inside this checkout.

    Walks up from this file looking for ``blockchain/abi/...`` rather than
    counting directory levels, because the answer changes depending on whether
    this runs from the source tree or an installed package -- and a wrong count
    produces a confusing "file not found" instead of a clear one.

    A packaged Worker carries the same file, unmodified, at the same relative
    path inside the bundle (see ``packaging/G-shared.spec``). It is looked for
    there first, so a packaged run never walks up into whatever folder the
    executable happens to have been copied into.
    """
    bundle = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and bundle:
        return Path(bundle) / ARTIFACT_RELATIVE_PATH
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / ARTIFACT_RELATIVE_PATH
        if candidate.is_file():
            return candidate
    return here.parents[3] / ARTIFACT_RELATIVE_PATH if len(here.parents) > 3 \
        else Path(ARTIFACT_RELATIVE_PATH)


class EscrowArtifact:
    """The parsed artifact: ABI, state numbering and provenance."""

    def __init__(self, payload: Mapping[str, Any], source: Path) -> None:
        self._payload = payload
        self.source = source

    @classmethod
    def load(cls, path: Path | None = None) -> "EscrowArtifact":
        artifact = Path(path) if path is not None else default_artifact_path()
        try:
            payload = json.loads(artifact.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise AbiUnavailable(
                f"the canonical escrow ABI is not at {artifact}. It is "
                "produced by the Blockchain module; this package does not "
                "carry a copy of it.") from None
        except (OSError, ValueError) as error:
            raise AbiUnavailable(
                f"the canonical escrow ABI at {artifact} could not be read "
                f"({type(error).__name__})") from None
        if not isinstance(payload, dict) or "abi" not in payload:
            raise AbiUnavailable(
                f"{artifact} does not look like a contract artifact")
        return cls(payload, artifact)

    # -- provenance, for reports and logs ------------------------------
    @property
    def contract_name(self) -> str:
        return str(self._payload.get("contractName", ""))

    @property
    def source_commit(self) -> str:
        return str(self._payload.get("sourceCommit", "unknown"))

    @property
    def solc_version(self) -> str:
        return str(self._payload.get("solcVersion", "unknown"))

    @property
    def deployed(self) -> bool:
        """Whether the Blockchain team has deployed this anywhere yet.

        ``False`` today. A Worker cannot reach a contract that does not exist,
        and saying so from the artifact is better than a confusing RPC error.
        """
        return bool(self._payload.get("deployed", False))

    @property
    def state_enum(self) -> dict[int, str]:
        """The contract's own state numbering, as it declares it."""
        raw = self._payload.get("stateEnum") or {}
        return {int(key): str(value) for key, value in raw.items()}

    @property
    def abi(self) -> list[dict[str, Any]]:
        return list(self._payload["abi"])

    # -- shape queries -------------------------------------------------
    def function(self, name: str) -> dict[str, Any]:
        for entry in self.abi:
            if entry.get("type") == "function" and entry.get("name") == name:
                return entry
        raise AbiMismatch(f"the contract has no function {name!r}")

    def event(self, name: str) -> dict[str, Any]:
        for entry in self.abi:
            if entry.get("type") == "event" and entry.get("name") == name:
                return entry
        raise AbiMismatch(f"the contract has no event {name!r}")

    def output_types(self, function_name: str) -> tuple[str, ...]:
        return tuple(output["type"]
                     for output in self.function(function_name)["outputs"])


#: The functions this package calls. Listed so a missing one is found when the
#: artifact is checked rather than when a Worker tries to sign.
REQUIRED_FUNCTIONS = ("jobs", "acceptJob", "abortJob", "isOperator")

#: The event that carries ``externalRef``. It is **not** a field of the job
#: struct, so this is the only way to check it.
REQUIRED_EVENTS = ("JobFunded",)


def check_contract_matches(artifact: EscrowArtifact, states: Mapping[int, str]) -> None:
    """Refuse an artifact that does not match what this package expects.

    ``states`` is this package's own :class:`~gshared_worker.escrow.JobState`,
    flattened to ``{value: name}``. Comparing the two is the whole point: it is
    the check that would have caught the last redesign the day it landed
    instead of months later.
    """
    if artifact.contract_name != EXPECTED_CONTRACT_NAME:
        raise AbiMismatch(
            f"expected {EXPECTED_CONTRACT_NAME}, artifact is "
            f"{artifact.contract_name!r}")

    declared = artifact.state_enum
    if declared and declared != dict(states):
        raise AbiMismatch(
            "the contract's JobState numbering does not match this build: "
            f"contract={declared}, worker={dict(states)}")

    for name in REQUIRED_FUNCTIONS:
        artifact.function(name)
    for name in REQUIRED_EVENTS:
        artifact.event(name)

    # The job struct is read positionally after decoding, so its width is part
    # of the contract as far as this package is concerned.
    outputs = artifact.output_types("jobs")
    if len(outputs) != 7:
        raise AbiMismatch(
            f"jobs() returns {len(outputs)} values, not the 7 this build "
            f"decodes: {outputs}")
