"""Getting base model weights onto this machine, pinned to one commit.

The training container runs with ``--network none`` and cannot fetch anything.
That is the point: a container holding a customer's data should not be able to
reach the internet. So the weights are fetched first, by something else, and
mounted read-only.

**That something else is a second, short-lived container.** It is the same
approved runtime image -- which already carries ``huggingface_hub`` and a
pinned dependency set -- started with a network and a cache mount, running a
download snippet this package owns. The alternatives were worse: adding
``huggingface_hub`` to the Worker would put a large dependency tree into a
program that is otherwise standard library and ctypes, and shelling out to
``git`` or ``curl`` would mean reimplementing the Hub's resolution rules.

**The revision must be a commit SHA.** ``main`` moves. A Job that was priced,
scheduled and accepted against one set of weights must not train against a
different set because somebody pushed to the branch in between -- and the
customer would have no way to tell, because both runs succeed.

**Nothing falls back to another model.** If ``Qwen/Qwen3-0.6B`` cannot be
fetched, the Job fails. Training a different model than the one that was asked
for produces an adapter that is useless in a way nobody discovers until they
try to load it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .job_workspace import ModelCache
from .logging_setup import LOGGER_NAME

_debug = logging.getLogger(LOGGER_NAME)

#: A mutable reference is not a pin. These are the ones that look like a
#: version and are not one.
MUTABLE_REFERENCES = frozenset({"main", "master", "latest", "head", "HEAD"})

_REVISION = re.compile(r"^[0-9a-f]{40}$")

MODEL_FETCH_TIMEOUT_SECONDS = 3600

#: Where the fetch container writes. Mounted from the host cache directory.
CONTAINER_CACHE_PATH = "/cache/model"

#: The download program. Ours, not a shell line: it runs in the container's
#: interpreter with the reference and revision passed as **arguments**, so
#: nothing from the Backend is ever interpolated into code. ``allow_patterns``
#: fetches weights, config and tokenizer only -- not the GGUF conversions or
#: ONNX exports, gigabytes this runtime will never open.
FETCH_PROGRAM = r'''import sys
from huggingface_hub import snapshot_download

ref, revision, out = sys.argv[1], sys.argv[2], sys.argv[3]
snapshot_download(
    repo_id=ref,
    revision=revision,
    local_dir=out,
    allow_patterns=["*.json", "*.safetensors", "*.txt", "*.py"],
)
print("GSHARED_MODEL_READY=" + ref + "@" + revision)
'''

#: Printed by the program above when it finished. Checked, because a container
#: that exits 0 having written nothing is a container that leaves an empty
#: directory looking like a model.
READY_MARKER = "GSHARED_MODEL_READY="


class ModelRefused(Exception):
    """The model cannot be prepared, and running without it is not an option."""

    def __init__(self, detail: str, *, fatal: bool = True) -> None:
        super().__init__(detail)
        self.detail = detail
        self.fatal = fatal


@dataclass(frozen=True)
class PreparedModel:
    path: Path
    model_ref: str
    revision: str
    #: Whether this run fetched it, or found it already there.
    fetched: bool


def check_revision(model_ref: str, revision: str) -> str:
    """A pinned commit, or a refusal naming what was sent instead."""
    text = (revision or "").strip()
    if not text:
        raise ModelRefused(
            f"the catalog has no pinned revision for {model_ref}, so there is "
            "no way to know which weights this Job was priced against")
    if text.lower() in MUTABLE_REFERENCES:
        raise ModelRefused(
            f"{model_ref} is pinned to {text!r}, which moves; this Worker "
            "runs exact commits only")
    if not _REVISION.match(text.lower()):
        raise ModelRefused(
            f"{text!r} is not a 40-character commit SHA")
    return text.lower()


def fetch_command(image_reference: str, host_directory: Path,
                  model_ref: str, revision: str) -> list[str]:
    """The preparation container.

    It has a network -- it is the only container in this pipeline that does --
    and nothing else. No GPU, no job workspace, no credential, a read-only
    root and a single writable mount that is the cache directory it fills.

    The reference and revision are **arguments to the program**, not text
    spliced into it. A model reference containing a quote would otherwise be a
    way to run code in this container.
    """
    return [
        "docker", "run", "--rm",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", "256",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=256m,uid=10001,gid=10001,mode=0700",
        "-e", "HF_HUB_OFFLINE=0",
        "-e", "HOME=/tmp",
        "-v", f"{host_directory}:{CONTAINER_CACHE_PATH}",
        "--entrypoint", "python",
        image_reference, "-c", FETCH_PROGRAM,
        model_ref, revision, CONTAINER_CACHE_PATH,
    ]


async def prepare_model(
    runner: Any,
    cache: ModelCache,
    image_reference: str,
    model_ref: str,
    revision: str,
    *,
    host_path_of: Any = None,
    timeout: float = MODEL_FETCH_TIMEOUT_SECONDS,
) -> PreparedModel:
    """The weights for exactly this revision, fetched once and reused after.

    ``host_path_of`` translates a Windows path into whatever the Docker daemon
    understands, because the daemon runs inside the WSL distribution and does
    not share the host's drive letters.
    """
    pinned = check_revision(model_ref, revision)
    directory = cache.directory_for(model_ref, pinned)

    if cache.is_complete(model_ref, pinned):
        _debug.debug("model: %s@%s already prepared", model_ref, pinned[:8])
        return PreparedModel(directory, model_ref, pinned, fetched=False)

    # An incomplete directory from a killed process is thrown away rather than
    # resumed. Resuming would mean trusting whatever is already there.
    cache.clear(model_ref, pinned)
    directory.mkdir(parents=True, exist_ok=True)

    mount = host_path_of(directory) if host_path_of else directory
    _debug.debug("model: fetching %s@%s", model_ref, pinned[:8])
    try:
        finished = await runner.run(
            fetch_command(image_reference, mount, model_ref, pinned),
            timeout=timeout, max_output_bytes=64 * 1024)
    except Exception as error:
        cache.clear(model_ref, pinned)
        raise ModelRefused(
            f"the model could not be fetched ({type(error).__name__})",
            fatal=False) from None

    if finished is None or finished.exit_code != 0:
        cache.clear(model_ref, pinned)
        raise ModelRefused(
            f"the model {model_ref} could not be fetched at revision "
            f"{pinned[:8]}", fatal=False)
    if READY_MARKER not in (finished.stdout or ""):
        # Exit 0 with nothing downloaded. An empty directory would otherwise
        # be marked complete and mounted as a model.
        cache.clear(model_ref, pinned)
        raise ModelRefused(
            "the model fetch reported success but did not finish writing")

    cache.mark_complete(model_ref, pinned)
    return PreparedModel(directory, model_ref, pinned, fetched=True)
