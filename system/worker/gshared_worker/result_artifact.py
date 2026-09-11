"""Turning a finished training run into the one file that gets uploaded.

The container writes three files. This packs exactly those three into a
``result.tar``, hashes it, and hands back the digest -- and the digest is the
whole point, because the same bytes go three places and all three must agree:

* the object in the Backend's storage,
* the ``sha256`` the Backend re-computes from that object,
* the ``bytes32 resultHash`` written on chain.

**The hash is of the artifact, not of the directory.** Not a manifest, not the
adapter on its own, not a hash of hashes. ``sha256(result.tar bytes)``, and the
bytes uploaded are the bytes hashed.

**The tar is deterministic.** The same three files produce the same archive
byte for byte, on any machine, at any time. That is not tidiness: a customer
who re-runs a verification has to arrive at the number that is on chain, and
an archive carrying the moment it was built would give them a different one
every time. So the entry order, the names, the ownership, the permissions and
the modification times are all fixed -- there is nothing in the file that
came from this machine.

**Three names, and only those three.** Whatever else ends up in a workspace --
a stray log, a cache directory, a symlink somebody planted -- is not in the
archive. It is a customer's dataset on a stranger's PC; the only things that
leave are the ones the customer paid for.
"""

from __future__ import annotations

import hashlib
import logging
import tarfile
from dataclasses import dataclass
from pathlib import Path

from .logging_setup import LOGGER_NAME

_debug = logging.getLogger(LOGGER_NAME)

#: What a successful QLoRA run writes, and the order they go into the archive.
#: The order is part of the contract: it is what makes the archive the same
#: twice, and ``Path.iterdir()`` is not ordered.
RESULT_FILES: tuple[str, ...] = (
    "adapter_config.json",
    "adapter_model.safetensors",
    "training_metrics.json",
)

#: Files that must have something in them. All three should, but an empty
#: metrics file is a diagnostic loss while an empty adapter is a broken
#: product, so both are refused rather than picking.
ARTIFACT_NAME = "result.tar"
ARTIFACT_CONTENT_TYPE = "application/x-tar"

#: Read in pieces. An adapter is tens to hundreds of megabytes and this
#: process also holds a WebSocket open.
CHUNK_BYTES = 1024 * 1024

#: The fixed metadata every entry gets.
FIXED_MTIME = 0
FIXED_MODE = 0o644
FIXED_UID = 0
FIXED_GID = 0

#: A ceiling, so a runaway output cannot fill the disk being copied.
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024 * 1024


class ArtifactRefused(Exception):
    """The output is not something that can be packaged."""


@dataclass(frozen=True)
class PackagedResult:
    """One archive, with the two numbers the rest of the flow needs."""

    path: Path
    sha256: str
    size_bytes: int

    @property
    def result_hash(self) -> str:
        """The on-chain form: ``0x`` and 64 hex digits, for ``bytes32``."""
        return "0x" + self.sha256


def check_output(output_dir: Path) -> list[Path]:
    """The three files, checked, in archive order. Raises rather than skips.

    Every check here is a second one -- the container validates its own output
    and exits 15 when it is wrong. This runs anyway, because "the container
    said so" is a claim by the thing being checked, and because between its
    exit and this call the directory sat on a supplier's disk.

    The symlink rules are the part that is not paranoia for its own sake. A
    link named ``adapter_config.json`` pointing at ``C:\\Users\\...\\.env``
    would be packaged, uploaded to the Backend and handed to a customer, and
    every other check here would pass.
    """
    root = _resolved(output_dir)
    if not root.is_dir():
        raise ArtifactRefused("the job wrote no output directory")

    found: list[Path] = []
    for name in RESULT_FILES:
        path = root / name
        if path.is_symlink():
            raise ArtifactRefused(f"{name} is a link, not a file")
        if not path.exists():
            raise ArtifactRefused(f"the job did not write {name}")
        if not path.is_file():
            raise ArtifactRefused(f"{name} is not a regular file")
        # After the symlink check, and against the resolved root: a directory
        # in the middle of the path could be a link even when the leaf is not.
        if _resolved(path).parent != root:
            raise ArtifactRefused(f"{name} resolves outside the job output")
        size = path.stat().st_size
        if size <= 0:
            raise ArtifactRefused(f"{name} is empty")
        if size > MAX_ARTIFACT_BYTES:
            raise ArtifactRefused(f"{name} is larger than this Worker will send")
        try:
            with path.open("rb") as stream:
                stream.read(1)
        except OSError as error:
            raise ArtifactRefused(
                f"{name} could not be read ({type(error).__name__})") from error
        found.append(path)
    return found


def pack_result(output_dir: Path, destination: Path) -> PackagedResult:
    """Write ``result.tar`` and return its digest and size.

    Written to a ``.part`` and renamed, so a packaging that was interrupted
    does not leave something that looks like a finished archive. The digest is
    taken from the finished file rather than accumulated while writing: what
    matters is the hash of the bytes that will be uploaded, and reading them
    back is how you know that is what they are.
    """
    files = check_output(output_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    if partial.exists():
        partial.unlink()

    try:
        # USTAR rather than the default PAX. A PAX archive carries extended
        # headers -- including, for some entries, the access and change times
        # -- and those are exactly the fields that would make two archives of
        # the same three files differ.
        with tarfile.open(partial, "w", format=tarfile.USTAR_FORMAT) as archive:
            for path in files:
                archive.addfile(_entry(path), _open(path))
    except OSError as error:
        partial.unlink(missing_ok=True)
        raise ArtifactRefused(
            f"the result could not be packaged ({type(error).__name__})"
        ) from error

    digest, size = hash_file(partial)
    partial.replace(destination)
    _debug.info("result: packaged %s bytes, sha256 %s...", size, digest[:12])
    return PackagedResult(destination, digest, size)


def _entry(path: Path) -> tarfile.TarInfo:
    """One archive entry, carrying nothing about this machine.

    The name is the bare filename, so the archive has no directory structure
    and nothing of the supplier's path in it. Owner, group, permissions and
    modification time are constants -- the uid this Worker happens to run as
    is not part of the customer's result, and it must not change the hash.
    """
    info = tarfile.TarInfo(path.name)
    info.size = path.stat().st_size
    info.mtime = FIXED_MTIME
    info.mode = FIXED_MODE
    info.type = tarfile.REGTYPE
    info.uid = FIXED_UID
    info.gid = FIXED_GID
    info.uname = ""
    info.gname = ""
    return info


def _open(path: Path):
    return path.open("rb")


def hash_file(path: Path) -> tuple[str, int]:
    """``(sha256 hex, size)`` read a piece at a time.

    Lowercase, no ``0x``. That is the form the Backend's
    ``ResultUploadCompleteRequest`` validates (``^[0-9a-fA-F]{64}$``, "0x
    없이") and the form its ``ObjectStat`` produces, so the two compare
    directly. The ``0x`` belongs to the chain and is added there.
    """
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path.absolute()
