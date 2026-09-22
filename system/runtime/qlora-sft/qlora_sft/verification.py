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
import re
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


# -- layer x module ------------------------------------------------------------

#: ``...layers.<n>.<any.path>.<module>.lora_<A|B>.<adapter>.weight`` -- the
#: shape of every PEFT LoRA name on a decoder whose blocks live in a
#: ``layers`` list, which is every model this runtime supports. Nothing about
#: the number of layers or which modules exist is assumed.
_LORA_NAME = re.compile(
    r"(?:^|\.)layers\.(\d+)\.(?:[A-Za-z0-9_]+\.)*?([A-Za-z0-9_]+)\.lora_([AB])\.")

#: Significant digits kept per heatmap cell. The values are small means
#: (around 1e-4); six digits is far below what a colour scale can show.
HEATMAP_DIGITS = 6


def parse_lora_name(name: str) -> tuple[int, str, str] | None:
    """``(layer, module, "A"|"B")`` from a LoRA parameter name, or ``None``."""
    match = _LORA_NAME.search(name)
    if match is None:
        return None
    return int(match.group(1)), match.group(2), match.group(3)


@dataclass(frozen=True)
class Heatmap:
    #: ``{"0": {"q_proj": 0.000132, ...}, ...}`` -- layer index as a string
    #: key (it is JSON), module short names. No raw parameter names.
    cells: dict
    #: A module in a layer with only one of A and B. LoRA always has both;
    #: this is a broken adapter, and it is reported rather than averaged.
    unpaired_count: int
    #: LoRA matrices whose name does not fit the layer pattern. They are still
    #: in the tensor counts; they just have no cell to go in.
    unmapped_count: int


def layer_module_delta_magnitude(checks: Sequence[TensorCheck]) -> Heatmap:
    """One small number per (layer, module): the mean of A's and B's delta.

    A cell is left out when either side's delta is not finite -- there is no
    honest number to draw. Layers come out in numeric order, modules in name
    order, so the same run always serialises the same way.
    """
    pairs: dict[tuple[int, str], dict[str, TensorCheck]] = {}
    unmapped = 0
    for check in checks:
        parsed = parse_lora_name(check.name)
        if parsed is None:
            unmapped += 1
            continue
        layer, module, side = parsed
        pairs.setdefault((layer, module), {})[side] = check

    cells: dict[str, dict[str, float]] = {}
    unpaired = 0
    for (layer, module) in sorted(pairs):
        sides = pairs[(layer, module)]
        if set(sides) != {"A", "B"}:
            unpaired += 1
            continue
        a, b = sides["A"].delta_mean_abs, sides["B"].delta_mean_abs
        if a is None or b is None:
            continue
        value = float(f"{(a + b) / 2:.{HEATMAP_DIGITS}g}")
        cells.setdefault(str(layer), {})[module] = value
    return Heatmap(cells=cells, unpaired_count=unpaired, unmapped_count=unmapped)


# -- what goes into training_metrics.json ------------------------------------

#: Bumped when a field in ``verification`` changes meaning or is removed.
#: Adding a field does not bump it; readers ignore what they do not know.
VERIFICATION_SCHEMA_VERSION = 1


def verification_report(checks: Sequence[TensorCheck]) -> dict:
    """The ``verification`` object for ``training_metrics.json``.

    ``valid`` is deliberately narrow. It is true when, and only when:

    * there is a LoRA structure (``tensorCount > 0``) and every module in it
      has both its A and its B matrix;
    * at least one LoRA matrix differs from its pre-training copy;
    * no LoRA value is NaN or Inf.

    It says the weights were updated and are numbers. It says nothing about
    whether the model got better, generalises, or learned the data -- nothing
    here measures that.
    """
    summary = aggregate(checks)
    heatmap = layer_module_delta_magnitude(checks)
    valid = (summary["tensorCount"] > 0
             and summary["changedTensorCount"] > 0
             and summary["nanCount"] == 0
             and summary["infCount"] == 0
             and heatmap.unpaired_count == 0)
    return {
        "schemaVersion": VERIFICATION_SCHEMA_VERSION,
        **summary,
        "valid": valid,
        "unpairedModuleCount": heatmap.unpaired_count,
        "unmappedTensorCount": heatmap.unmapped_count,
        "layerModuleDeltaMagnitude": heatmap.cells,
    }
