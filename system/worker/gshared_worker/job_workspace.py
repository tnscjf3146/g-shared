"""Where a Job's files live on this machine, and why the paths are built here.

Every path in the runtime pipeline is derived from a Job id or a model
reference, and both of those arrive from the Backend. A path built by joining
a message's contents onto a directory is a path an attacker chooses: ``../``
walks out of the workspace, an absolute path ignores it entirely, and on
Windows a name like ``CON`` or ``a:b`` does something stranger still.

So nothing here concatenates. Identifiers are checked against a narrow pattern
first, and anything that does not match is refused rather than sanitised --
sanitising turns two different Jobs into one directory, which is worse than
refusing either.

Two separate places, on purpose:

* **the model cache** is shared between Jobs and keyed by reference *and*
  revision, because the same weights are worth fetching once;
* **the Job workspace** is per Job and is not shared, because a dataset is a
  signed, customer-owned object and one Job's input is not another's.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

#: Job ids as the Backend issues them: a decimal database key, as text. A
#: pattern rather than an escape, so "12/../../etc" is refused instead of
#: quietly becoming "12".
_JOB_ID = re.compile(r"^[0-9]{1,32}$")

#: A Hugging Face repository: ``owner/name``, both restricted.
_MODEL_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}/"
                        r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

#: A git commit SHA. Exactly forty hex digits -- nothing shorter, and never a
#: branch name.
_REVISION = re.compile(r"^[0-9a-f]{40}$")

#: Written into a model directory once it is complete. Its absence means a
#: download was interrupted, and an interrupted model is a model that loads
#: with some shards missing.
COMPLETE_MARKER = ".gshared-complete"

#: The file name the container's contract expects under /job/input.
DATASET_FILE_NAME = "dataset.jsonl"


class UnsafePath(ValueError):
    """An identifier that will not be turned into a path."""


def safe_job_id(job_id: str) -> str:
    if not isinstance(job_id, str) or not _JOB_ID.match(job_id):
        raise UnsafePath(f"{job_id!r} is not a usable Job id")
    return job_id


def safe_model_key(model_ref: str, revision: str) -> str:
    """One directory name for exactly one set of weights.

    The revision is part of the key, not a file inside the directory. Two
    revisions of one repository are two different models, and a cache that
    kept them under one name would hand a Job the wrong weights and never
    notice -- the files would load perfectly.
    """
    if not isinstance(model_ref, str) or not _MODEL_REF.match(model_ref):
        raise UnsafePath(f"{model_ref!r} is not a usable model reference")
    if not isinstance(revision, str) or not _REVISION.match(revision.lower()):
        raise UnsafePath(
            "a model revision must be a 40-character commit SHA; "
            f"{revision!r} is not one")
    owner, _, name = model_ref.partition("/")
    return f"{owner}__{name}__{revision.lower()}"


@dataclass(frozen=True)
class JobWorkspace:
    """The directories one Job owns.

    Deliberately **not** deleted when the Job finishes. The output is what
    task 9 uploads, and a pipeline that tidied up after itself would leave
    nothing to upload.
    """

    root: Path
    job_id: str

    @classmethod
    def under(cls, base: Path, job_id: str) -> "JobWorkspace":
        checked = safe_job_id(job_id)
        return cls(root=Path(base) / "jobs" / checked, job_id=checked)

    @property
    def input_dir(self) -> Path:
        return self.root / "input"

    @property
    def output_dir(self) -> Path:
        return self.root / "output"

    @property
    def metadata_dir(self) -> Path:
        return self.root / "metadata"

    @property
    def dataset_path(self) -> Path:
        return self.input_dir / DATASET_FILE_NAME

    def prepare(self) -> "JobWorkspace":
        """Make the directories, empty of anything an earlier attempt left.

        The input directory is cleared because a dataset from a previous
        attempt at the same Job is a file this run did not verify. The output
        directory is cleared because a half-written adapter beside a new one
        is indistinguishable from a complete one.
        """
        for directory in (self.input_dir, self.output_dir):
            if directory.exists():
                shutil.rmtree(directory)
        for directory in (self.input_dir, self.output_dir, self.metadata_dir):
            directory.mkdir(parents=True, exist_ok=True)
        return self


@dataclass(frozen=True)
class ModelCache:
    """Weights, shared between Jobs, keyed by reference and revision."""

    root: Path

    def directory_for(self, model_ref: str, revision: str) -> Path:
        return self.root / "models" / safe_model_key(model_ref, revision)

    def is_complete(self, model_ref: str, revision: str) -> bool:
        """Whether a previous fetch actually finished.

        An existing directory is not enough. A process killed mid-download
        leaves one behind that looks entirely reasonable and is missing a
        shard -- so the marker, written last, is what is checked.
        """
        directory = self.directory_for(model_ref, revision)
        return (directory / COMPLETE_MARKER).is_file()

    def mark_complete(self, model_ref: str, revision: str) -> Path:
        directory = self.directory_for(model_ref, revision)
        (directory / COMPLETE_MARKER).write_text(
            f"{model_ref}@{revision}\n", encoding="utf-8")
        return directory

    def clear(self, model_ref: str, revision: str) -> None:
        """Throw away an incomplete attempt so the next one starts clean."""
        directory = self.directory_for(model_ref, revision)
        if directory.exists():
            shutil.rmtree(directory, ignore_errors=True)


def to_distro_path(path: Path) -> str:
    r"""A Windows path as the Docker daemon inside the distribution sees it.

    The daemon runs in WSL and has no drive letters. ``C:\S15P21E204\x``
    reaches it as ``/mnt/c/S15P21E204/x``; a path that is already POSIX is
    handed back unchanged so tests and Linux hosts need no special case.

    Returned as a string rather than a Path because it is going straight into
    a ``docker -v`` argument, and :class:`pathlib.Path` on Windows would turn
    the separators back.
    """
    text = str(path)
    drive, colon, rest = text.partition(":")
    if colon and len(drive) == 1 and drive.isalpha():
        return "/mnt/" + drive.lower() + rest.replace("\\", "/")
    return text.replace("\\", "/")
