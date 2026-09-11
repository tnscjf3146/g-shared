"""The container's entry point: run a job, and say plainly how it went.

This is the only place an exception becomes an exit code, and the only place
anything is printed that was not an event. Everything below it either returns
metrics or raises :class:`~qlora_sft.failures.TrainingFailure`.

The bare ``except Exception`` at the bottom is on purpose. A training stack is
thousands of lines of somebody else's code and it will one day raise something
nobody here predicted; when it does, the Worker gets a documented non-zero
exit and a ``failed`` event rather than a Python traceback on stdout and an
exit code of 1 that means nothing in particular. The traceback's message is
deliberately not forwarded -- it can carry a path from inside the container --
so the type name is what travels.
"""

from __future__ import annotations

import sys

from .cli import read_request
from .events import EventWriter
from .failures import ERROR_CODES, ExitCode, TrainingFailure


def main(argv: list | None = None, events: EventWriter | None = None) -> int:
    events = events or EventWriter()
    try:
        request = read_request(argv)
        from . import train

        train.run(request, events)
        return int(ExitCode.OK)
    except TrainingFailure as failure:
        events.failed(error_code=failure.error_code, message=failure.detail)
        return int(failure.code)
    except KeyboardInterrupt:
        # A cancelled job is not a training failure. Task 8 will have a
        # JOB_CANCEL to say so; until then the exit code is the honest one.
        events.failed(
            error_code=ERROR_CODES[ExitCode.TRAINING_FAILED],
            message="the run was interrupted")
        return int(ExitCode.TRAINING_FAILED)
    except Exception as error:
        events.failed(
            error_code=ERROR_CODES[ExitCode.TRAINING_FAILED],
            message="the run failed unexpectedly (" + type(error).__name__ + ")")
        return int(ExitCode.TRAINING_FAILED)


if __name__ == "__main__":
    sys.exit(main())
