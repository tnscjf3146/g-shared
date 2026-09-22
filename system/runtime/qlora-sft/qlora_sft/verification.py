"""Evidence, written into ``training_metrics.json``, that training did something.

A job that exits 0 with an adapter file on disk proves the file exists. It
does not prove the weights in it moved. This module produces the numbers that
do, and only numbers: nothing here writes a raw weight anywhere.

Everything in this file that does not need torch is plain Python, so the
host's test run -- which has no torch -- still covers the parts that decide
what goes into the metrics.
"""

from __future__ import annotations

import math
from typing import Iterable, Mapping

#: The most points ``lossHistory`` carries. A long run logs a loss every
#: optimizer step; a chart needs a few hundred at most, and the Backend reads
#: no more than this many either.
MAX_LOSS_POINTS = 200


def _finite_number(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def loss_history(log_history: Iterable[Mapping], limit: int = MAX_LOSS_POINTS) -> list[dict]:
    """``[{"step", "loss"}]`` per optimizer step, from the Trainer's log history.

    ``trainer.state.log_history`` holds one entry per logging event. With
    ``logging_steps=1`` that is one per optimizer step, and ``step`` is the
    Trainer's ``global_step`` -- optimizer steps, not micro batches, so
    gradient accumulation does not multiply the points. The final summary
    entry (``train_loss``, ``train_runtime``) has no ``loss`` key and is not a
    point.

    * A step logged twice keeps its last value.
    * Steps come out in ascending order whatever order they went in.
    * A non-finite loss is left out. The metrics file is written with
      ``allow_nan=False`` -- NaN is not JSON -- and the rest of the run's
      behaviour is not this function's to change: whether a NaN loss fails a
      job is decided elsewhere, exactly as before.
    * Over ``limit`` points, the curve is sampled evenly across the whole run,
      always keeping the first and last step, so the shape and the final loss
      survive.
    """
    by_step: dict[int, float] = {}
    for entry in log_history or ():
        if not isinstance(entry, Mapping):
            continue
        step, loss = entry.get("step"), entry.get("loss")
        if isinstance(step, bool) or not isinstance(step, int) or step < 0:
            continue
        if not _finite_number(loss):
            continue
        by_step[step] = float(loss)
    points = [{"step": step, "loss": round(by_step[step], 6)} for step in sorted(by_step)]
    return downsample(points, limit)


def downsample(points: list, limit: int = MAX_LOSS_POINTS) -> list:
    """At most ``limit`` items, evenly spread, first and last always kept."""
    count = len(points)
    if count <= limit:
        return list(points)
    if limit < 2:
        raise ValueError("a curve needs at least its first and last point")
    # The same index rule the Backend uses when it trims a long history, so a
    # history that arrives already trimmed is not trimmed differently twice.
    return [points[round(i * (count - 1) / (limit - 1))] for i in range(limit)]


# -- the LoRA weights before training -----------------------------------------

#: How a LoRA parameter is named by PEFT, wherever it sits in the model:
#: ``...q_proj.lora_A.default.weight``. The base model's own weights never
#: carry these, so a name test is enough to leave all of them out.
LORA_MARKERS = (".lora_A.", ".lora_B.")


def is_lora_parameter(name: str, parameter) -> bool:
    """A trainable LoRA A or B matrix. Base weights are frozen and unnamed so."""
    return (bool(getattr(parameter, "requires_grad", False))
            and any(marker in name for marker in LORA_MARKERS))


def snapshot_lora_parameters(model) -> dict:
    """A CPU copy of every trainable LoRA matrix, keyed by parameter name.

    Taken after the adapters are attached and before the first optimizer step,
    so a later comparison can tell *changed* from *non-zero*: PEFT initialises
    ``lora_A`` randomly, so it is non-zero before anything has been learned.

    * Only LoRA parameters. The base model is billions of values and is frozen;
      copying it would cost gigabytes to learn nothing.
    * One copy, straight to CPU float32, with ``copy=True`` so the snapshot
      never aliases the live parameter -- not on the GPU, and not when the
      parameter already happens to be a CPU float32 tensor (as in tests).
      No GPU tensor is referenced once this returns.
    * The key is the name from ``named_parameters()``. The same model object is
      compared after training, so the names match one to one.

    Nothing here is written anywhere. The snapshot lives in memory only for the
    comparison.
    """
    import torch

    snapshot = {}
    for name, parameter in model.named_parameters():
        if is_lora_parameter(name, parameter):
            snapshot[name] = parameter.detach().to(
                device="cpu", dtype=torch.float32, copy=True)
    return dict(sorted(snapshot.items()))
