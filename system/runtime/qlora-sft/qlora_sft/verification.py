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
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

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


# -- before against after ------------------------------------------------------


@dataclass(frozen=True)
class TensorCheck:
    """What one LoRA matrix looked like after training, against its baseline.

    Counts, flags and one mean. Never the values themselves.
    """

    name: str
    nan_count: int
    inf_count: int
    non_zero: bool
    changed: bool
    #: ``mean(|after - before|)`` -- how far training moved this matrix, not
    #: how large its values are. ``None`` when the difference is not finite
    #: (a NaN or Inf in the result makes the mean meaningless).
    delta_mean_abs: float | None

    @property
    def finite(self) -> bool:
        return self.nan_count == 0 and self.inf_count == 0


def compare_tensor(name: str, before, after) -> TensorCheck:
    """Compare one LoRA matrix with its pre-training copy.

    **Changed means different from the baseline, not different from zero.**
    ``lora_A`` starts random and ``lora_B`` starts at zero, so "non-zero" says
    nothing about whether anything was learned.

    **Exact comparison, no epsilon.** Both sides are the same parameter widened
    to float32 the same way, so an untouched matrix compares bit-for-bit equal
    and there is no rounding noise for a tolerance to absorb. Any optimizer
    step that reaches a matrix changes some of its bits. A tolerance would only
    add a way to call a small real update "unchanged".
    """
    import torch

    current = after.detach().to(device="cpu", dtype=torch.float32, copy=True)
    if tuple(current.shape) != tuple(before.shape):
        raise ValueError(f"{name} changed shape during training")
    nan_count = int(torch.isnan(current).sum().item())
    inf_count = int(torch.isinf(current).sum().item())
    delta = (current - before).abs()
    if delta.numel() == 0:
        delta_mean_abs = 0.0
    elif bool(torch.isfinite(delta).all()):
        delta_mean_abs = float(delta.mean().item())
    else:
        delta_mean_abs = None
    return TensorCheck(
        name=name,
        nan_count=nan_count,
        inf_count=inf_count,
        non_zero=bool(torch.count_nonzero(current).item() > 0),
        changed=not torch.equal(current, before),
        delta_mean_abs=delta_mean_abs,
    )


def compare_lora_parameters(baseline: Mapping, model) -> list[TensorCheck]:
    """Every baselined LoRA matrix against the same-named parameter now.

    The set of LoRA parameters must be the one that was snapshotted: a matrix
    that disappeared or appeared during training is a structural problem, not
    something to average over, so it raises.
    """
    live = {name: parameter for name, parameter in model.named_parameters()
            if is_lora_parameter(name, parameter)}
    if set(live) != set(baseline):
        missing = sorted(set(baseline) - set(live))
        extra = sorted(set(live) - set(baseline))
        raise ValueError(f"LoRA parameters differ from the baseline "
                         f"(missing {len(missing)}, new {len(extra)})")
    return [compare_tensor(name, baseline[name], live[name]) for name in sorted(baseline)]


def aggregate(checks: Sequence[TensorCheck]) -> dict:
    """Counts over every LoRA matrix. Nothing about the model is assumed:
    the number of layers and target modules comes from what was compared."""
    return {
        "tensorCount": len(checks),
        "changedTensorCount": sum(1 for c in checks if c.changed),
        "nonZeroTensorCount": sum(1 for c in checks if c.non_zero),
        "nanCount": sum(c.nan_count for c in checks),
        "infCount": sum(c.inf_count for c in checks),
    }
