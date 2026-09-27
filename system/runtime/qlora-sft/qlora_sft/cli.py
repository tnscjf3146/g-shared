"""The container's command line: one runner, both models.

There is no ``--model qwen3-0.6b`` switch and no branch on model size
anywhere below. The runner is given a directory of weights and a set of
numbers, and the difference between the two-minute demo and the hour-long
standard job is entirely in those two inputs. A second code path per model is
a second code path to test, and the one that runs less often is the one that
breaks.

Paths default to the mount contract in the README (``/job/model``,
``/job/input/dataset.jsonl``, ``/job/output``) so the usual invocation is just
the training numbers, but every one of them can be overridden -- which is what
makes the runner testable outside a container.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from .config import TrainingConfig, read_config
from .failures import ExitCode, TrainingFailure

DEFAULT_MODEL_PATH = "/job/model"
DEFAULT_DATASET_PATH = "/job/input/dataset.jsonl"
DEFAULT_OUTPUT_DIR = "/job/output"


@dataclass(frozen=True)
class TrainingRequest:
    model_path: Path
    dataset_path: Path
    output_dir: Path
    config: TrainingConfig
    #: Stops after this many optimizer steps. Only ever set by hand, for the
    #: smoke tests and the calibration run in the docs -- a real job trains
    #: for whole epochs and this is None.
    max_steps: int | None = None
    #: A LoRA adapter to continue training from. None trains a fresh one,
    #: which is what every job did before this existed.
    adapter_path: Path | None = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qlora-sft",
        description="QLoRA supervised fine-tuning for one job, on one GPU.")
    parser.add_argument("--model-path", default=DEFAULT_MODEL_PATH)
    parser.add_argument("--dataset-path", default=DEFAULT_DATASET_PATH)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--adapter-path", default=None,
        help="A LoRA adapter directory to continue training from "
             "(adapter_config.json + adapter_model.safetensors). "
             "Absent means a freshly initialised adapter.")
    parser.add_argument("--epochs", type=int, required=True)
    parser.add_argument("--batch-size", type=int, required=True)
    parser.add_argument("--learning-rate", type=float, required=True)
    parser.add_argument("--max-length", type=int, required=True)
    parser.add_argument("--seed", type=int, default=20260910)
    parser.add_argument(
        "--gradient-accumulation-steps", type=int, default=1,
        help="Optimizer steps see this many batches. The AI Scheduler's "
             "batchSize is what goes on the GPU at once and is not changed.")
    parser.add_argument(
        "--gradient-checkpointing", action="store_true",
        help="Trade compute for VRAM. Off unless asked for.")
    parser.add_argument(
        "--max-steps", type=int, default=None,
        help=argparse.SUPPRESS)
    return parser


def read_request(argv: list[str] | None = None) -> TrainingRequest:
    """Parse and validate, before anything expensive happens.

    ``argparse`` exits with 2 on a malformed command line, which is neither
    of our codes; the numbers below are checked here so that a bad
    ``--epochs 0`` is INVALID_INPUT like every other bad input.
    """
    args = build_parser().parse_args(argv)
    config = read_config(
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        max_length=args.max_length,
        seed=args.seed,
        gradient_checkpointing=args.gradient_checkpointing,
        gradient_accumulation_steps=args.gradient_accumulation_steps)

    if args.max_steps is not None and args.max_steps < 1:
        raise TrainingFailure(
            ExitCode.INVALID_INPUT, "maxSteps must be at least 1")

    return TrainingRequest(
        model_path=Path(args.model_path),
        dataset_path=Path(args.dataset_path),
        output_dir=Path(args.output_dir),
        config=config,
        max_steps=args.max_steps,
        adapter_path=Path(args.adapter_path) if args.adapter_path else None)


def steps_for(samples: int, config: TrainingConfig,
              max_steps: int | None = None) -> int:
    """How many optimizer steps this run will take.

    Task 8 will report ``currentStep / totalSteps``, so this number has to be
    known before the first step rather than discovered at the end. It matches
    what ``transformers.Trainer`` does with ``drop_last=False``: a final part
    batch is still a batch.
    """
    per_step = config.batch_size * config.gradient_accumulation_steps
    per_epoch = max(1, -(-samples // per_step))
    total = per_epoch * config.epochs
    if max_steps is not None:
        return min(total, max_steps)
    return total
