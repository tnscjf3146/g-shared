"""Fetching a Job's dataset, and proving it is the one that was promised.

The Backend sends a signed URL and a SHA-256. Both matter, and the second is
the reason this module is not three lines of ``urlretrieve``.

**The hash is recomputed, never taken on trust.** The Backend calculated it
server-side when the customer uploaded the file, and the Worker calculates it
again from the bytes that actually arrived. A signed URL can expire mid-stream,
a proxy can truncate, storage can return an error page with a 200. Every one of
those produces a file that looks like a file, and the hash is the only thing
that tells them apart from the real one.

**The URL is a credential.** Its query string carries a signature that grants
read access to a customer's data for as long as it is valid, so it is never
logged, never put in an error message, and never used to name a file on disk.

Streaming, in fixed-size chunks: a dataset can be hundreds of megabytes and a
Worker that read one into memory to hash it would be a Worker that fell over
on the job it was paid for.
"""

from __future__ import annotations

import hashlib
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .logging_setup import LOGGER_NAME

_debug = logging.getLogger(LOGGER_NAME)

#: Read size. Large enough that hashing is not syscall-bound, small enough
#: that a stop signal is noticed promptly.
CHUNK_BYTES = 1024 * 1024

DOWNLOAD_TIMEOUT_SECONDS = 300
DOWNLOAD_ATTEMPTS = 3
DOWNLOAD_FIRST_WAIT_SECONDS = 1.0

#: A dataset larger than this is refused before a byte is written. The limit
#: is not a policy about customers -- it is a floor under "the disk filled up
#: because a header said 40 GB".
MAX_DATASET_BYTES = 8 * 1024 * 1024 * 1024

#: Partial downloads live here until they are verified. The final name is only
#: ever created by a rename, so a dataset file that exists is a dataset file
#: that was checked.
PARTIAL_SUFFIX = ".part"


class DatasetRefused(Exception):
    """The dataset could not be fetched, or is not the promised one."""

    def __init__(self, detail: str, *, fatal: bool = True) -> None:
        super().__init__(detail)
        self.detail = detail
        self.fatal = fatal


@dataclass(frozen=True)
class FetchedDataset:
    path: Path
    size_bytes: int
    sha256: str


def check_url(url: str) -> str:
    """An http(s) URL, or nothing. Never logged.

    ``file://`` would read the Worker's own disk. Anything more exotic hands
    the choice of protocol handler to whoever wrote the field.
    """
    parsed = urllib.parse.urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        raise DatasetRefused(
            "the dataset download URL is not an http(s) address")
    if not parsed.netloc:
        raise DatasetRefused("the dataset download URL names no host")
    return url


def fetch_dataset(
    url: str,
    destination: Path,
    *,
    expected_sha256: str,
    expected_size: int,
    opener: Callable[..., Any] | None = None,
    sleep: Callable[[float], bool] | None = None,
    attempts: int = DOWNLOAD_ATTEMPTS,
) -> FetchedDataset:
    """Download, hash and size-check. The file only appears if all three pass.

    Retries are for the transfer, not for the verification. A hash mismatch is
    not tried again: the server said what the file should be, and what arrived
    was something else. Asking for it a second time cannot change which of
    those is wrong, and a Worker that kept asking until the numbers agreed
    would eventually train on whatever it was handed.
    """
    check_url(url)
    if expected_size > MAX_DATASET_BYTES:
        raise DatasetRefused(
            f"the dataset is {expected_size} bytes, which is beyond what this "
            "Worker will download")
    wanted = _checked_hex(expected_sha256)

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + PARTIAL_SUFFIX)

    waiting = DOWNLOAD_FIRST_WAIT_SECONDS
    last = ""
    for attempt in range(1, attempts + 1):
        try:
            size, digest = _stream(url, partial, opener)
        except DatasetRefused:
            partial.unlink(missing_ok=True)
            raise
        except Exception as error:
            # Only the type. A urllib error's message can contain the URL, and
            # the URL is the credential.
            last = f"the download did not finish ({type(error).__name__})"
            partial.unlink(missing_ok=True)
            _debug.info("dataset: %s (attempt %s/%s)", last, attempt, attempts)
            if attempt == attempts:
                break
            if sleep is not None and not sleep(waiting):
                raise DatasetRefused(
                    "the Worker is stopping, so the dataset was not fetched",
                    fatal=False) from None
            waiting *= 2
            continue

        # Verified before the file is given its real name, so a dataset that
        # exists on disk is one that passed both checks.
        if size != expected_size:
            partial.unlink(missing_ok=True)
            raise DatasetRefused(
                f"the dataset is {size} bytes and the service said "
                f"{expected_size}")
        if digest != wanted:
            partial.unlink(missing_ok=True)
            raise DatasetRefused(
                "the dataset does not match the SHA-256 the service gave for "
                "it")

        destination.unlink(missing_ok=True)
        partial.replace(destination)
        _debug.info("dataset: %s bytes, sha256 %s...", size, digest[:12])
        return FetchedDataset(destination, size, digest)

    raise DatasetRefused(
        f"the dataset could not be fetched after {attempts} attempts: {last}",
        fatal=False)


def _stream(url: str, partial: Path,
            opener: Callable[..., Any] | None) -> tuple[int, str]:
    """One attempt: bytes to disk, hash as they go. Never all in memory."""
    request = urllib.request.Request(url, method="GET")
    open_it = opener or urllib.request.urlopen
    digest = hashlib.sha256()
    size = 0

    with open_it(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
        with partial.open("wb") as handle:
            while True:
                chunk = response.read(CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_DATASET_BYTES:
                    raise DatasetRefused(
                        "the dataset is larger than this Worker will download")
                digest.update(chunk)
                handle.write(chunk)
    return size, digest.hexdigest()


def _checked_hex(value: str) -> str:
    """The Backend's format: 64 hex digits, no ``0x``."""
    text = (value or "").strip().lower()
    if text.startswith("0x"):
        # The escrow's result hash carries 0x; a file hash does not. Accepting
        # both quietly would let a 0x-prefixed value compare unequal for ever.
        text = text[2:]
    if len(text) != 64 or any(c not in "0123456789abcdef" for c in text):
        raise DatasetRefused(
            "the service did not give a usable SHA-256 for the dataset")
    return text
