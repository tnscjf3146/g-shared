"""What a finished adapter looks like, in numbers a screen can draw.

``_check_output`` answers "is there an adapter and does it load". That passes
for an adapter whose ``lora_B`` is still the zeros PEFT initialised it to --
a run that trained nothing loads perfectly and changes no output. The counts
here answer the next question instead: did the weights actually move, and did
any of them go to NaN or Inf on the way.

The split is deliberate. Turning tensors into :class:`TensorStat` rows needs
torch and a loaded model; everything after that is arithmetic. Only the first
part lives in ``train.py``, so this module -- the part with the decisions in
it -- is tested without a GPU.

The result is written into ``training_metrics.json`` under ``verification``,
which Backend hands to the job detail screen untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

#: Where a LoRA parameter name says which transformer block it belongs to.
#: PEFT names them ``...model.layers.<n>.self_attn.q_proj.lora_A.default.weight``.
_LAYER_MARKER = ".layers."
_LORA_MARKER = ".lora_"

#: Magnitudes are fractions of a percent; six decimals would round most cells
#: to zero and flatten the heatmap. Nine keeps the differences and still
#: stops float noise from making every number 17 digits long in the JSON.
_DECIMALS = 9


@dataclass(frozen=True)
class TensorStat:
    """One adapter tensor, already reduced to the facts that matter.

    ``delta`` is the mean absolute change against the same tensor before
    training -- a mean rather than a sum so that ``lora_A`` and ``lora_B``,
    which have different element counts, stay comparable.
    """

    name: str
    delta: float
    changed: bool
    non_zero: bool
    has_nan: bool
    has_inf: bool


def parse_target(name: str) -> tuple[str, str] | None:
    """``(layer, module)`` for the heatmap, or ``None`` if it has no cell.

    ``None`` is a normal answer, not a failure: an adapter may hold tensors
    that do not sit on a numbered transformer block, and those are still
    counted -- they just are not drawn.
    """
    lora = name.find(_LORA_MARKER)
    if lora < 0:
        return None
    layer_at = name.rfind(_LAYER_MARKER, 0, lora)
    if layer_at < 0:
        return None

    rest = name[layer_at + len(_LAYER_MARKER):lora]
    layer, _, module_path = rest.partition(".")
    if not layer.isdigit() or not module_path:
        return None
    # "self_attn.q_proj" and "mlp.gate_proj" both end in the projection, which
    # is the name the screen shows.
    return layer, module_path.rsplit(".", 1)[-1]


def summarize(stats: Iterable[TensorStat]) -> dict:
    """The ``verification`` block of ``training_metrics.json``."""
    rows = list(stats)
    heatmap: dict[str, dict[str, float]] = {}
    for row in rows:
        cell = parse_target(row.name)
        if cell is None:
            continue
        layer, module = cell
        modules = heatmap.setdefault(layer, {})
        # A module is one cell and has two tensors, so they are summed. In
        # practice the cell is lora_B's number: lora_B starts at exactly zero
        # while lora_A starts Kaiming-initialised, so their update magnitudes
        # differ by orders of magnitude. That is consistent across every cell,
        # which is all the screen's min/max normalisation needs.
        modules[module] = round(modules.get(module, 0.0) + row.delta, _DECIMALS)

    nan_count = sum(1 for row in rows if row.has_nan)
    inf_count = sum(1 for row in rows if row.has_inf)
    changed = sum(1 for row in rows if row.changed)
    return {
        "tensorCount": len(rows),
        "changedTensorCount": changed,
        "nonZeroTensorCount": sum(1 for row in rows if row.non_zero),
        "nanCount": nan_count,
        "infCount": inf_count,
        # Weights moved and none of them went bad. Both halves are needed:
        # an all-zero adapter has no NaN in it either.
        #
        # None when there were no tensors to look at, which is a different
        # event from "it trained nothing" and has to stay distinguishable:
        # the screen turns False into a red "검증 이상 발견" badge and only
        # leaves the badge off for null. A job that completed fine should not
        # be accused because the parameter names were not the ones this knows.
        "valid": (changed > 0 and nan_count == 0 and inf_count == 0
                  if rows else None),
        # For whoever reads the file: "2" before "10". The screen sorts the
        # keys itself, and _write_metrics re-sorts them as strings on the way
        # out, so nothing downstream relies on this.
        "layerModuleDeltaMagnitude": {
            layer: heatmap[layer]
            for layer in sorted(heatmap, key=int)
        },
    }
