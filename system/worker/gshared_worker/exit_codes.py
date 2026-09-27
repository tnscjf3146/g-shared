"""Stable Worker process results, so a launcher never parses output."""

from __future__ import annotations

from enum import IntEnum


class WorkerExitCode(IntEnum):
    """The exact set of results the Worker process may return to its launcher.

    The numbers are a contract read by whatever started the process, so they are
    never renumbered. Two of them cannot be produced yet -- nothing on the
    current startup path talks to the Backend -- and they stay here because the
    launcher's side of the contract is the whole set, not the part in use today.
    """

    OK = 0
    #: Something nobody planned for. The log holds it; the screen points there.
    UNEXPECTED_FAULT = 1
    EXPECTED_FAILURE = 2
    TRANSPORT_FAILURE = 3
    CREDENTIAL_MISMATCH = 4
    ALREADY_RUNNING = 5

    @property
    def issue_code(self) -> str:
        """The stable, secret-free name for this result, safe to show and log."""
        return _ISSUE_CODES[self]


_ISSUE_CODES = {
    WorkerExitCode.OK: "WORKER_OK",
    WorkerExitCode.UNEXPECTED_FAULT: "WORKER_UNEXPECTED_FAULT",
    WorkerExitCode.EXPECTED_FAILURE: "WORKER_EXPECTED_FAILURE",
    WorkerExitCode.TRANSPORT_FAILURE: "WORKER_TRANSPORT_FAILURE",
    WorkerExitCode.CREDENTIAL_MISMATCH: "WORKER_CREDENTIAL_MISMATCH",
    WorkerExitCode.ALREADY_RUNNING: "WORKER_ALREADY_RUNNING",
}
