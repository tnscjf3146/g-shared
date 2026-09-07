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


class CommandError(WorkerError):
    """A Windows command could not be run, timed out, or said far too much.

    Carries a code as well as a message because the caller decides what an
    unanswerable command means: for the environment diagnosis every one of them
    means the same thing -- this machine did not answer -- while a caller that
    acts on the result needs to tell a missing tool from a slow one.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
