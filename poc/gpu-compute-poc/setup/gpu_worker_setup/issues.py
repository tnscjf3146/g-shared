"""Safe, user-facing Setup issue model."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Severity(str, Enum):
    """How Setup should present an issue."""

    INFO = "INFO"
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"


class Retryability(str, Enum):
    """How an operator may recover from an issue."""

    TRANSIENT = "TRANSIENT"
    USER_ACTION = "USER_ACTION"
    PERMANENT = "PERMANENT"


@dataclass(frozen=True)
class SetupIssue:
    """A non-secret issue suitable for Setup UI and structured logging."""

    stage: str
    code: str
    severity: Severity
    retryability: Retryability
    user_message: str
    diagnostic_id: str
