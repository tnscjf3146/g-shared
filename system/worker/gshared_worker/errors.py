"""Expected Worker failures, so a fault is told apart from a mistake.

Only the errors the current startup path can raise live here. The GPU, Docker
and Backend errors the PoC also defines belong with the code that raises them
and arrive with those branches.
"""


class WorkerError(RuntimeError):
    """Base class for failures the Worker anticipated and can explain."""


class WorkerConfigurationError(WorkerError):
    """The Worker configuration is missing, unreadable or invalid."""


class WorkerAlreadyRunningError(WorkerError):
    """Another Worker process already owns this user/Worker ID/GPU identity."""
