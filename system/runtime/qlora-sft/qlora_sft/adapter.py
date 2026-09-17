"""An initial LoRA adapter to continue training from, checked before the GPU.

A job may bring the adapter an earlier job produced -- the same two files this
runtime writes to ``/job/output`` -- and train on from there instead of from a
freshly initialised one. The Worker unpacks it under ``/job/input/adapter``.

Everything here is checked on the host's Python, without torch, before any
weights are loaded. What cannot be checked without loading is whether the
adapter's shapes fit *this* base model; :mod:`qlora_sft.train` finds that out
when PEFT attaches it and reports it as INVALID_INPUT like the rest.

**Why not compare the base model's name.** A written ``adapter_config.json``
records ``base_model_name_or_path`` as whatever path the model was loaded from,
and inside this container that is always ``/job/model``. The name says nothing
about which weights were there, so it is not read as evidence either way.

**What is recorded.** The SHA-256 of the two files that were actually loaded
goes into ``training_metrics.json``. It is how a finished job proves which
adapter it started from, rather than claiming it did.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .failures import ExitCode, TrainingFailure

ADAPTER_CONFIG_FILE = "adapter_config.json"
ADAPTER_WEIGHTS_FILE = "adapter_model.safetensors"

#: What LoRA training in this runtime is. Any other PEFT method would load and
#: then train something this runtime was never tested with.
SUPPORTED_PEFT_TYPE = "LORA"
SUPPORTED_TASK_TYPE = "CAUSAL_LM"

#: A directory holding one of these is a base model, not an adapter. Loading
#: it as an adapter fails later and less clearly.
BASE_WEIGHT_FILES = (
    "model.safetensors", "pytorch_model.bin", "model.safetensors.index.json",
    "pytorch_model.bin.index.json",
)

#: Pickle formats run code when they are loaded. This runtime only ever reads
#: safetensors, and an adapter directory that carries one of these is refused
#: rather than partly trusted.
PICKLE_FILES = ("adapter_model.bin", "adapter_model.pt")

_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class InitAdapter:
    """An adapter directory that passed every check that needs no GPU."""

    path: Path
    config: dict[str, Any]
    config_sha256: str
    weights_sha256: str

    def describe(self) -> dict[str, Any]:
        """What ``training_metrics.json`` records about it. No path."""
        targets = self.config.get("target_modules")
        if isinstance(targets, list):
            targets = sorted(str(name) for name in targets)
        return {
            "weightsSha256": self.weights_sha256,
            "configSha256": self.config_sha256,
            "r": self.config.get("r"),
            "loraAlpha": self.config.get("lora_alpha"),
            "targetModules": targets,
        }


def check_adapter_directory(path: Path | str) -> InitAdapter:
    """The adapter the job brought, or INVALID_INPUT saying what is wrong.

    Messages name a file this runtime expects, never the path it was given.
    """
    directory = Path(path)
    if not directory.is_dir():
        raise TrainingFailure(
            ExitCode.INVALID_INPUT, "the initial adapter directory does not exist")

    for name in (ADAPTER_CONFIG_FILE, ADAPTER_WEIGHTS_FILE):
        if not (directory / name).is_file():
            raise TrainingFailure(
                ExitCode.INVALID_INPUT, f"the initial adapter has no {name}")
    for name in BASE_WEIGHT_FILES:
        if (directory / name).exists():
            raise TrainingFailure(
                ExitCode.INVALID_INPUT,
                f"the initial adapter holds {name}, which is a base model "
                "rather than an adapter")
    for name in PICKLE_FILES:
        if (directory / name).exists():
            raise TrainingFailure(
                ExitCode.INVALID_INPUT,
                f"the initial adapter holds {name}; only safetensors is read")

    if (directory / ADAPTER_WEIGHTS_FILE).stat().st_size == 0:
        raise TrainingFailure(
            ExitCode.INVALID_INPUT, "the initial adapter's weights file is empty")

    config = _read_config(directory / ADAPTER_CONFIG_FILE)
    return InitAdapter(
        path=directory,
        config=config,
        config_sha256=_sha256(directory / ADAPTER_CONFIG_FILE),
        weights_sha256=_sha256(directory / ADAPTER_WEIGHTS_FILE),
    )


def _read_config(config_file: Path) -> dict[str, Any]:
    try:
        config = json.loads(config_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise TrainingFailure(
            ExitCode.INVALID_INPUT,
            "the initial adapter's adapter_config.json could not be read") from None
    if not isinstance(config, dict):
        raise TrainingFailure(
            ExitCode.INVALID_INPUT,
            "the initial adapter's adapter_config.json is not an object")

    if config.get("peft_type") != SUPPORTED_PEFT_TYPE:
        raise TrainingFailure(
            ExitCode.INVALID_INPUT,
            f"the initial adapter is {config.get('peft_type')!r}, and this "
            f"runtime continues only {SUPPORTED_PEFT_TYPE} adapters")
    task_type = config.get("task_type")
    if task_type is not None and task_type != SUPPORTED_TASK_TYPE:
        raise TrainingFailure(
            ExitCode.INVALID_INPUT,
            f"the initial adapter was made for {task_type!r}, not "
            f"{SUPPORTED_TASK_TYPE}")

    rank = config.get("r")
    if isinstance(rank, bool) or not isinstance(rank, int) or rank <= 0:
        raise TrainingFailure(
            ExitCode.INVALID_INPUT, "the initial adapter has no usable rank (r)")

    targets = config.get("target_modules")
    usable_list = (isinstance(targets, list) and targets
                   and all(isinstance(name, str) and name for name in targets))
    if not (usable_list or (isinstance(targets, str) and targets)):
        raise TrainingFailure(
            ExitCode.INVALID_INPUT,
            "the initial adapter names no target modules")
    return config


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()
