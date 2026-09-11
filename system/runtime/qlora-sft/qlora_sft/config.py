"""The numbers a training run is given, and the models it will accept.

Two things are checked here, and both are checked before a single weight is
read, because loading a 1.7B model in order to discover that ``epochs`` was
zero wastes a minute of somebody's GPU.

**The config is never adjusted.** Not to fit the GPU, not to avoid an
out-of-memory error, not because a value looks unusual. When the mode is
CUSTOM these numbers are what a customer typed and paid against, and a
container that quietly trained something else would be lying to both ends of
the job. Choosing numbers is the AI Scheduler's job; this module's job is to
refuse the ones that are not numbers at all.

**The model is checked by what it is, not by who sent it.** The container is
handed a directory. It reads that directory's own ``config.json`` and
requires an architecture this runtime has actually been tested against. A
Backend primary key is not part of this contract -- see :data:`CATALOG` for
why the reference and revision are, and are recorded rather than trusted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .failures import ExitCode, TrainingFailure


@dataclass(frozen=True)
class CatalogEntry:
    """A base model this runtime supports, pinned to one commit.

    ``revision`` is a Hugging Face commit SHA and never ``main``. A tag moves;
    a job priced and scheduled against one set of weights must not train
    against another set next week under the same name.
    """

    ref: str
    revision: str
    display_name: str
    #: What the AI Scheduler's AUTO rules pair this with in the demo. Recorded
    #: for documentation only -- the runtime is given numbers, not a mode.
    intended_mode: str


#: The two supported base models. Both revisions were read from the Hugging
#: Face model API and resolve; neither was written from memory.
CATALOG = {
    "qwen3-0.6b": CatalogEntry(
        ref="Qwen/Qwen3-0.6B",
        revision="c1899de289a04d12100db370d81485cdf75e47ca",
        display_name="Qwen3 0.6B",
        intended_mode="SPEED",
    ),
    "qwen3-1.7b": CatalogEntry(
        ref="Qwen/Qwen3-1.7B",
        revision="70d244cc86ccca08cf5af4e1e306ecf908b1ad5e",
        display_name="Qwen3 1.7B",
        intended_mode="BALANCED",
    ),
}

#: The one architecture this runtime has been run against. A directory holding
#: anything else is refused rather than attempted: QLoRA target modules,
#: quantisation behaviour and the prompt markers in
#: :mod:`qlora_sft.dataset` were all checked against this family and nothing
#: else.
SUPPORTED_MODEL_TYPE = "qwen3"
SUPPORTED_ARCHITECTURE = "Qwen3ForCausalLM"

#: Bounds, not preferences. They exist to catch a malformed request, not to
#: express an opinion about good hyper-parameters -- which is why they are
#: this wide.
MAX_EPOCHS = 100
MAX_BATCH_SIZE = 1024
MAX_LENGTH_LIMIT = 32768


@dataclass(frozen=True)
class TrainingConfig:
    """Exactly what the Backend's ``trainingConfig`` carries, plus a seed.

    The first four fields are the AI Scheduler's contract and are used as
    given. ``seed`` and ``gradient_checkpointing`` are the runtime's own: they
    change how a run is executed, not what was asked for.
    """

    epochs: int
    batch_size: int
    learning_rate: float
    max_length: int
    seed: int = 20260910
    gradient_checkpointing: bool = False
    gradient_accumulation_steps: int = 1

    @property
    def tokens_per_step(self) -> int:
        return self.batch_size * self.max_length


def read_config(
    *,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    max_length: int,
    seed: int = 20260910,
    gradient_checkpointing: bool = False,
    gradient_accumulation_steps: int = 1,
) -> TrainingConfig:
    """The config, or a refusal naming the field that was wrong.

    Booleans are rejected where an integer is expected. ``True`` is an ``int``
    in Python and would sail through every range check as a batch size of one,
    which is a silently different run rather than an error.
    """
    _whole("epochs", epochs, 1, MAX_EPOCHS)
    _whole("batchSize", batch_size, 1, MAX_BATCH_SIZE)
    _whole("maxLength", max_length, 1, MAX_LENGTH_LIMIT)
    _whole("gradientAccumulationSteps", gradient_accumulation_steps, 1, 1024)
    _whole("seed", seed, 0, 2 ** 31 - 1)
    _rate(learning_rate)
    if not isinstance(gradient_checkpointing, bool):
        raise TrainingFailure(
            ExitCode.INVALID_INPUT, "gradientCheckpointing must be a boolean")
    return TrainingConfig(
        epochs=epochs, batch_size=batch_size, learning_rate=learning_rate,
        max_length=max_length, seed=seed,
        gradient_checkpointing=gradient_checkpointing,
        gradient_accumulation_steps=gradient_accumulation_steps)


def _whole(name: str, value: object, low: int, high: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TrainingFailure(
            ExitCode.INVALID_INPUT, f"{name} must be a whole number")
    if not low <= value <= high:
        raise TrainingFailure(
            ExitCode.INVALID_INPUT,
            f"{name} must be between {low} and {high}, and is not")


def _rate(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TrainingFailure(
            ExitCode.INVALID_INPUT, "learningRate must be a number")
    rate = float(value)
    # NaN fails every comparison, so it is caught by the range test rather
    # than sneaking past it -- but only because the test is written this way
    # round. ``not 0 < nan`` is True.
    if not 0.0 < rate < 1.0:
        raise TrainingFailure(
            ExitCode.INVALID_INPUT,
            "learningRate must be greater than 0 and less than 1")


def check_model_directory(model_path: Path) -> dict:
    """Read the model's own config and refuse anything untested.

    Returns the parsed ``config.json``. The check is on ``model_type`` and
    ``architectures`` because those are what the directory says about itself;
    a caller's claim about which model this is has no weight here.
    """
    config_file = Path(model_path) / "config.json"
    if not config_file.is_file():
        raise TrainingFailure(
            ExitCode.MODEL_LOAD_FAILED,
            "the model directory has no config.json")
    try:
        config = json.loads(config_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise TrainingFailure(
            ExitCode.MODEL_LOAD_FAILED,
            "the model's config.json could not be read") from None
    if not isinstance(config, dict):
        raise TrainingFailure(
            ExitCode.MODEL_LOAD_FAILED,
            "the model's config.json is not an object")

    model_type = config.get("model_type")
    architectures = config.get("architectures") or []
    if model_type != SUPPORTED_MODEL_TYPE:
        raise TrainingFailure(
            ExitCode.MODEL_LOAD_FAILED,
            f"this runtime trains {SUPPORTED_MODEL_TYPE} models and the "
            f"model directory holds {model_type!r}")
    if SUPPORTED_ARCHITECTURE not in architectures:
        raise TrainingFailure(
            ExitCode.MODEL_LOAD_FAILED,
            f"this runtime trains {SUPPORTED_ARCHITECTURE} and the model "
            f"directory declares {architectures!r}")
    return config
