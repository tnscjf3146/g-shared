"""How this container reports that it did not train a model.

Two contracts live here, and both are read by a program rather than a person:
the **exit code**, which is what ``docker wait`` returns, and the **error
code**, which is the stable name that will one day travel in a JOB_FAILED
message. Neither is ever renumbered or renamed.

There is deliberately no exception hierarchy beyond one class. A training
container has one job and a handful of ways to not do it; a framework of
subclasses would be more code than the thing it describes.

Nothing here formats a path, a token or a URL into a message. What the Worker
logs from this container may end up in front of a supplier, and the container
is never the place that decides a secret is safe to print -- see
:mod:`qlora_sft.events`, which is the only writer of these.
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """What the process returns. Zero means an adapter was written."""

    OK = 0
    #: The request could not be read: bad JSONL, a missing field, a config
    #: value outside its range. Nothing was loaded.
    INVALID_INPUT = 10
    #: The weights under ``/job/model`` are not a model this runtime trains.
    MODEL_LOAD_FAILED = 11
    #: No usable NVIDIA GPU. This runtime does not train on a CPU, so this is
    #: a failure rather than a slower run.
    CUDA_UNAVAILABLE = 12
    #: The GPU ran out of memory. Never retried and never quietly shrunk --
    #: the config is the caller's, not ours.
    CUDA_OOM = 13
    #: Training itself raised.
    TRAINING_FAILED = 14
    #: Training finished and what it left behind is not a loadable adapter.
    OUTPUT_VALIDATION_FAILED = 15


#: The stable, secret-free name for each failure. These are the strings that
#: will eventually be carried by JOB_FAILED, so they are part of the contract
#: even though nothing sends them yet.
ERROR_CODES = {
    ExitCode.OK: "OK",
    ExitCode.INVALID_INPUT: "INVALID_INPUT",
    ExitCode.MODEL_LOAD_FAILED: "MODEL_LOAD_FAILED",
    ExitCode.CUDA_UNAVAILABLE: "CUDA_UNAVAILABLE",
    ExitCode.CUDA_OOM: "CUDA_OOM",
    ExitCode.TRAINING_FAILED: "TRAINING_FAILED",
    ExitCode.OUTPUT_VALIDATION_FAILED: "OUTPUT_VALIDATION_FAILED",
}


class TrainingFailure(Exception):
    """A failure this runtime recognises, with the code it exits on.

    ``detail`` is written to stdout as-is, so callers put a reason in it and
    never a path, an argument dump or the contents of an input file.
    """

    def __init__(self, code: ExitCode, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail

    @property
    def error_code(self) -> str:
        return ERROR_CODES[self.code]
