"""Stable Worker process results that Setup reads instead of parsing output."""

from __future__ import annotations

from enum import IntEnum


class WorkerExitCode(IntEnum):
    """The exact set of results the Worker process may return to its launcher."""

    OK = 0
    EXPECTED_FAILURE = 2
    TRANSPORT_FAILURE = 3
    CREDENTIAL_MISMATCH = 4
    ALREADY_RUNNING = 5

    @property
    def issue_code(self) -> str:
        """The stable, secret-free code Setup shows and logs for this result."""
        return _ISSUE_CODES[self]

    @property
    def is_install_failure(self) -> bool:
        """Whether Setup should treat this result as the installation failing.

        ``ALREADY_RUNNING`` is not a failure: another Worker process already owns
        this identity, so Setup verifies that existing Worker's Backend state
        rather than reporting a broken install.
        """
        return self not in _SUCCESSFUL_RESULTS


_ISSUE_CODES = {
    WorkerExitCode.OK: "WORKER_OK",
    WorkerExitCode.EXPECTED_FAILURE: "WORKER_EXPECTED_FAILURE",
    WorkerExitCode.TRANSPORT_FAILURE: "WORKER_TRANSPORT_FAILURE",
    WorkerExitCode.CREDENTIAL_MISMATCH: "WORKER_CREDENTIAL_MISMATCH",
    WorkerExitCode.ALREADY_RUNNING: "WORKER_ALREADY_RUNNING",
}

_SUCCESSFUL_RESULTS = frozenset({WorkerExitCode.OK, WorkerExitCode.ALREADY_RUNNING})
