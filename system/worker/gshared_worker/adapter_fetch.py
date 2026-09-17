"""Fetching a Job's initial LoRA adapter, and unpacking it without trusting it.

A Job may continue training from an adapter the customer uploaded -- usually
the ``result.tar`` an earlier Job produced. It arrives the way a dataset does:
a signed URL and the SHA-256 the Backend computed from the stored object. The
download is checked the same way, for the same reasons (see
:mod:`gshared_worker.dataset_fetch`).

What is new is that this file is an **archive**, and an archive is a set of
instructions for writing files. ``tarfile.extractall`` follows them: a member
named ``../../x`` writes outside the directory, a symlink member points a later
write anywhere on the disk, and a device member is a device. So nothing here
extracts. Every member is checked against a short list of exact names, only
regular files are accepted, and each one is copied out by hand into a
directory this module chose.

The contract (shared with the Backend and the Frontend):

* an **uncompressed** ``.tar``, at most :data:`MAX_ADAPTER_BYTES`;
* at its root exactly ``adapter_config.json`` and ``adapter_model.safetensors``,
  optionally ``training_metrics.json`` -- so a finished Job's result can be
  handed straight back;
* nothing else: no directories, no links, no ``./`` prefix, no other names.

The URL is a credential and never appears in a message or a log line.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import tarfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .logging_setup import LOGGER_NAME

_debug = logging.getLogger(LOGGER_NAME)

CHUNK_BYTES = 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 300
DOWNLOAD_ATTEMPTS = 3
DOWNLOAD_FIRST_WAIT_SECONDS = 1.0

#: A LoRA adapter is tens to hundreds of megabytes. The ceiling is the
#: contract's, and it is enforced before a byte is written and while writing.
MAX_ADAPTER_BYTES = 1024 * 1024 * 1024

ADAPTER_CONFIG_FILE = "adapter_config.json"
ADAPTER_WEIGHTS_FILE = "adapter_model.safetensors"
METRICS_FILE = "training_metrics.json"

REQUIRED_FILES = (ADAPTER_CONFIG_FILE, ADAPTER_WEIGHTS_FILE)
ALLOWED_FILES = frozenset(REQUIRED_FILES + (METRICS_FILE,))

PARTIAL_SUFFIX = ".part"


class AdapterRefused(Exception):
    """The adapter could not be fetched, or is not one this Worker will run."""

    def __init__(self, detail: str, *, fatal: bool = True) -> None:
        super().__init__(detail)
        self.detail = detail
        self.fatal = fatal


@dataclass(frozen=True)
class FetchedAdapter:
    directory: Path
    size_bytes: int
    sha256: str
    files: tuple[str, ...]


def fetch_adapter(
    url: str,
    archive_path: Path,
    adapter_dir: Path,
    *,
    expected_sha256: str,
    expected_size: int,
    opener: Callable[..., Any] | None = None,
    sleep: Callable[[float], bool] | None = None,
    attempts: int = DOWNLOAD_ATTEMPTS,
) -> FetchedAdapter:
    """Download, verify, unpack. ``adapter_dir`` exists only if all of it passed.

    The archive itself is removed once unpacked: the container is given the
    directory, and a second copy of a few hundred megabytes helps nobody.
    """
    _check_url(url)
    if expected_size <= 0 or expected_size > MAX_ADAPTER_BYTES:
        raise AdapterRefused(
            f"the adapter is {expected_size} bytes, outside what this Worker "
            f"accepts (1 to {MAX_ADAPTER_BYTES})")
    wanted = _checked_hex(expected_sha256)

    archive_path = Path(archive_path)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    partial = archive_path.with_name(archive_path.name + PARTIAL_SUFFIX)

    waiting = DOWNLOAD_FIRST_WAIT_SECONDS
    last = ""
    for attempt in range(1, attempts + 1):
        try:
            size, digest = _stream(url, partial, opener)
        except AdapterRefused:
            partial.unlink(missing_ok=True)
            raise
        except Exception as error:
            # The type only: a urllib message can carry the URL.
            last = f"the download did not finish ({type(error).__name__})"
            partial.unlink(missing_ok=True)
            _debug.warning("adapter: %s (attempt %s/%s)", last, attempt, attempts)
            if attempt == attempts:
                break
            if sleep is not None and not sleep(waiting):
                raise AdapterRefused(
                    "the Worker is stopping, so the adapter was not fetched",
                    fatal=False) from None
            waiting *= 2
            continue

        if size != expected_size:
            partial.unlink(missing_ok=True)
            raise AdapterRefused(
                f"the adapter is {size} bytes and the service said {expected_size}")
        if digest != wanted:
            partial.unlink(missing_ok=True)
            raise AdapterRefused(
                "the adapter does not match the SHA-256 the service gave for it")

        archive_path.unlink(missing_ok=True)
        partial.replace(archive_path)
        try:
            files = unpack_adapter(archive_path, adapter_dir)
        finally:
            archive_path.unlink(missing_ok=True)
        _debug.debug("adapter: %s bytes, sha256 %s..., %s", size, digest[:12], files)
        return FetchedAdapter(Path(adapter_dir), size, digest, files)

    raise AdapterRefused(
        f"the adapter could not be fetched after {attempts} attempts: {last}",
        fatal=False)


def unpack_adapter(archive_path: Path, destination: Path) -> tuple[str, ...]:
    """Copy the allowed members out by hand. Never ``extract``/``extractall``.

    ``destination`` is emptied first and removed again if anything is wrong,
    so a directory that exists afterwards holds a complete, checked adapter.
    """
    destination = Path(destination)
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    try:
        written = _copy_members(Path(archive_path), destination)
        _check_unpacked(destination, written)
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return tuple(sorted(written))


def _copy_members(archive_path: Path, destination: Path) -> set[str]:
    try:
        # "r:" -- uncompressed only. A .tar.gz is a different contract, and
        # decompressing an attacker's stream is one more thing to bound.
        archive = tarfile.open(archive_path, mode="r:")
    except (tarfile.TarError, OSError):
        raise AdapterRefused("the adapter is not an uncompressed tar archive") from None

    written: set[str] = set()
    total = 0
    with archive:
        try:
            for member in archive:
                name = member.name
                if name not in ALLOWED_FILES:
                    # Also what "./adapter_config.json", "../x", "/etc/x",
                    # "adapter/..." and every directory entry end up as.
                    raise AdapterRefused(
                        "the adapter archive holds an entry other than "
                        + ", ".join(sorted(ALLOWED_FILES)))
                if not member.isreg():
                    raise AdapterRefused(
                        f"{name} in the adapter archive is not a regular file")
                if name in written:
                    raise AdapterRefused(
                        f"the adapter archive holds {name} more than once")
                total += member.size
                if member.size < 0 or total > MAX_ADAPTER_BYTES:
                    raise AdapterRefused(
                        "the adapter archive unpacks to more than this Worker accepts")
                _copy_one(archive, member, destination / name)
                written.add(name)
        except tarfile.TarError:
            raise AdapterRefused("the adapter archive is damaged") from None
    return written


def _copy_one(archive: tarfile.TarFile, member: tarfile.TarInfo, target: Path) -> None:
    source = archive.extractfile(member)
    if source is None:
        raise AdapterRefused(f"{member.name} in the adapter archive could not be read")
    copied = 0
    with source, target.open("wb") as handle:
        while True:
            chunk = source.read(CHUNK_BYTES)
            if not chunk:
                break
            copied += len(chunk)
            if copied > member.size:
                raise AdapterRefused(
                    f"{member.name} in the adapter archive is longer than it says")
            handle.write(chunk)
    if copied != member.size:
        raise AdapterRefused(f"{member.name} in the adapter archive is truncated")


def _check_unpacked(destination: Path, written: set[str]) -> None:
    for name in REQUIRED_FILES:
        if name not in written:
            raise AdapterRefused(f"the adapter archive has no {name}")
    if (destination / ADAPTER_WEIGHTS_FILE).stat().st_size == 0:
        raise AdapterRefused("the adapter's weights file is empty")
    try:
        config = json.loads((destination / ADAPTER_CONFIG_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise AdapterRefused("the adapter's adapter_config.json is not JSON") from None
    if not isinstance(config, dict):
        raise AdapterRefused("the adapter's adapter_config.json is not an object")
    # Whether it is a LoRA that fits this base model is the runtime's check,
    # made with the model in hand. This one only makes sure it was handed
    # something shaped like an adapter.


def _stream(url: str, partial: Path,
            opener: Callable[..., Any] | None) -> tuple[int, str]:
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
                if size > MAX_ADAPTER_BYTES:
                    raise AdapterRefused(
                        "the adapter is larger than this Worker will download")
                digest.update(chunk)
                handle.write(chunk)
    return size, digest.hexdigest()


def _check_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        raise AdapterRefused("the adapter download URL is not an http(s) address")
    if not parsed.netloc:
        raise AdapterRefused("the adapter download URL names no host")


def _checked_hex(value: str) -> str:
    """64 hex digits, no ``0x`` -- the Backend's file-hash format."""
    text = (value or "").strip().lower()
    if len(text) != 64 or any(c not in "0123456789abcdef" for c in text):
        raise AdapterRefused("the service did not give a usable SHA-256 for the adapter")
    return text
