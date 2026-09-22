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
