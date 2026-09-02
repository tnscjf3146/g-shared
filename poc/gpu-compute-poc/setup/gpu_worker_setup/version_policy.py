"""Semantic Worker versions and the embedded-payload update policy."""

from __future__ import annotations

from dataclasses import dataclass
from functools import total_ordering
import re
from typing import Protocol


_SEMVER = re.compile(
    r"^(?P<major>0|[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)\.(?P<patch>0|[1-9][0-9]*)"
    r"(?:-(?P<prerelease>(?:0|[1-9][0-9]*|[0-9A-Za-z-]+)(?:\.(?:0|[1-9][0-9]*|[0-9A-Za-z-]+))*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


@total_ordering
@dataclass(frozen=True)
class WorkerVersion:
    """A SemVer 2.0.0 value whose build metadata has no precedence."""

    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()
    build: tuple[str, ...] = ()

    @classmethod
    def parse(cls, value: str) -> "WorkerVersion":
        if not isinstance(value, str):
            raise ValueError("Worker version must be a SemVer string")
        match = _SEMVER.fullmatch(value)
        if match is None:
            raise ValueError(f"Worker version is not valid SemVer: {value!r}")
        prerelease = tuple(match.group("prerelease").split(".")) if match.group("prerelease") else ()
        build = tuple(match.group("build").split(".")) if match.group("build") else ()
        if any(
            _is_ascii_numeric_identifier(part)
            and len(part) > 1
            and part.startswith("0")
            for part in prerelease
        ):
            raise ValueError(f"Worker version is not valid SemVer: {value!r}")
        return cls(
            int(match.group("major")),
            int(match.group("minor")),
            int(match.group("patch")),
            prerelease,
            build,
        )

    def __str__(self) -> str:
        value = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            value += "-" + ".".join(self.prerelease)
        if self.build:
            value += "+" + ".".join(self.build)
        return value

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, WorkerVersion):
            return NotImplemented
        return self._precedence_key() == other._precedence_key()

    def __hash__(self) -> int:
        return hash(self._precedence_key())

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, WorkerVersion):
            return NotImplemented
        own_core = (self.major, self.minor, self.patch)
        other_core = (other.major, other.minor, other.patch)
        if own_core != other_core:
            return own_core < other_core
        return _prerelease_less_than(self.prerelease, other.prerelease)

    def _precedence_key(self) -> tuple[int, int, int, tuple[str, ...]]:
        return (self.major, self.minor, self.patch, self.prerelease)

    def _identity_key(self) -> tuple[int, int, int, tuple[str, ...], tuple[str, ...]]:
        return (self.major, self.minor, self.patch, self.prerelease, self.build)


@dataclass(frozen=True)
class VersionMetadata:
    """Optional Backend policy data; absent fields intentionally remain absent."""

    minimum_supported_worker_version: WorkerVersion | None = None
    recommended_worker_version: WorkerVersion | None = None
    latest_worker_version: WorkerVersion | None = None


@dataclass(frozen=True)
class RollbackContext:
    """Journal evidence that permits only the transaction's previous version."""

    previous_version: WorkerVersion
    previous_manifest_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.previous_version, WorkerVersion):
            raise ValueError("rollback previous_version must be a WorkerVersion")
        if not isinstance(self.previous_manifest_sha256, str) or not _SHA256.fullmatch(
            self.previous_manifest_sha256
        ):
            raise ValueError("rollback previous_manifest_sha256 must be 64 hexadecimal characters")


@dataclass(frozen=True)
class VersionDecision:
    """The policy outcome used by install/update and future job acceptance gates."""

    allowed: bool
    receive_work_allowed: bool
    update_required: bool
    update_recommended: bool
    reason_code: str


class VersionPolicy(Protocol):
    """The policy boundary for local and future Backend-provided version rules."""

    def evaluate(
        self,
        current: WorkerVersion,
        candidate: WorkerVersion,
        metadata: VersionMetadata,
        rollback_context: RollbackContext | None = None,
    ) -> VersionDecision: ...


class LocalVersionPolicy:
    """Local policy for the embedded payload PoC without invented Backend data."""

    def evaluate(
        self,
        current: WorkerVersion,
        candidate: WorkerVersion,
        metadata: VersionMetadata,
        rollback_context: RollbackContext | None = None,
    ) -> VersionDecision:
        _validate_versions(current, candidate, metadata)
        minimum_unsupported = (
            metadata.minimum_supported_worker_version is not None
            and current < metadata.minimum_supported_worker_version
        )

        if candidate < current and not _is_exact_rollback(candidate, rollback_context):
            return VersionDecision(
                False,
                not minimum_unsupported,
                minimum_unsupported,
                False,
                "WORKER_DOWNGRADE_BLOCKED",
            )

        if minimum_unsupported:
            return VersionDecision(True, False, True, False, "WORKER_VERSION_UNSUPPORTED")

        if (
            metadata.recommended_worker_version is not None
            and current < metadata.recommended_worker_version
        ):
            return VersionDecision(True, True, False, True, "WORKER_UPDATE_RECOMMENDED")

        if candidate < current:
            return VersionDecision(True, True, False, False, "WORKER_ROLLBACK_ALLOWED")
        if candidate == current:
            return VersionDecision(True, True, False, False, "WORKER_VERSION_CURRENT")
        return VersionDecision(True, True, False, False, "WORKER_UPDATE_ALLOWED")


def _prerelease_less_than(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    if not left:
        return False
    if not right:
        return True
    for left_part, right_part in zip(left, right, strict=False):
        if left_part == right_part:
            continue
        left_numeric = _is_ascii_numeric_identifier(left_part)
        right_numeric = _is_ascii_numeric_identifier(right_part)
        if left_numeric and right_numeric:
            return int(left_part) < int(right_part)
        if left_numeric != right_numeric:
            return left_numeric
        return left_part < right_part
    return len(left) < len(right)


def _validate_versions(
    current: WorkerVersion, candidate: WorkerVersion, metadata: VersionMetadata
) -> None:
    if not isinstance(current, WorkerVersion) or not isinstance(candidate, WorkerVersion):
        raise ValueError("current and candidate must be WorkerVersion values")
    if not isinstance(metadata, VersionMetadata):
        raise ValueError("metadata must be VersionMetadata")
    for version in (
        metadata.minimum_supported_worker_version,
        metadata.recommended_worker_version,
        metadata.latest_worker_version,
    ):
        if version is not None and not isinstance(version, WorkerVersion):
            raise ValueError("policy metadata versions must be WorkerVersion values")


def _is_exact_rollback(
    candidate: WorkerVersion, rollback_context: RollbackContext | None
) -> bool:
    return (
        rollback_context is not None
        and rollback_context.previous_version._identity_key() == candidate._identity_key()
    )


def _is_ascii_numeric_identifier(value: str) -> bool:
    return bool(value) and all("0" <= character <= "9" for character in value)
