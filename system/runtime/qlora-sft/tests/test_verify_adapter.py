"""The tensor side of adapter verification, without a GPU.

``verification.summarize`` is arithmetic over rows and is tested on its own.
This covers the step before it: reading the live LoRA parameters, comparing
them against the snapshot taken before the first step, and -- the part worth
pinning -- not letting a bad tensor take the whole metrics file down with it.

``_write_metrics`` calls ``json.dumps(..., allow_nan=False)``. A NaN weight
produces a NaN mean, and a NaN in the heatmap raises there: the run would
train for its full time, write the adapter, validate it, and then die at the
last line. The count reports the NaN; the magnitude must not carry it.
"""

from __future__ import annotations

import math
import unittest

from qlora_sft.train import _adapter_baseline, _verify_adapter

Q_PROJ = "base_model.model.model.layers.0.self_attn.q_proj.lora_B.default.weight"
K_PROJ = "base_model.model.model.layers.0.self_attn.k_proj.lora_B.default.weight"
BASE = "base_model.model.model.layers.0.self_attn.q_proj.base_layer.weight"


class Tensor:
    """Only the handful of operations ``_verify_adapter`` performs."""

    def __init__(self, values):
        self.values = [float(v) for v in values]

    @property
    def shape(self):
        return (len(self.values),)

    def detach(self):
        return self

    def to(self, _device, copy=False):
        return Tensor(self.values) if copy else self

    def float(self):
        return self

    def __sub__(self, other):
        return Tensor([a - b for a, b in zip(self.values, other.values)])

    def abs(self):
        return Tensor([abs(v) for v in self.values])

    def mean(self):
        return sum(self.values) / len(self.values) if self.values else 0.0

    def ne(self, number):
        return _Mask(v != number for v in self.values)


class _Mask:
    def __init__(self, flags):
        self.flags = list(flags)

    def any(self):
        return any(self.flags)


class Torch:
    @staticmethod
    def isnan(tensor):
        return _Mask(math.isnan(v) for v in tensor.values)

    @staticmethod
    def isinf(tensor):
        return _Mask(math.isinf(v) for v in tensor.values)


class Model:
    def __init__(self, parameters):
        self._parameters = parameters

    def named_parameters(self):
        return list(self._parameters.items())


def verify(before: dict, after: dict) -> dict:
    model = Model({name: Tensor(values) for name, values in after.items()})
    baseline = {name: Tensor(values) for name, values in before.items()}
    return _verify_adapter(Torch(), model, baseline)


class Baseline(unittest.TestCase):

    def test_only_lora_parameters_are_snapshotted(self):
        """The 4-bit base is gigabytes and never changes -- copying it would
        be the whole point of the adapter, undone."""
        model = Model({Q_PROJ: Tensor([0.0]), BASE: Tensor([1.0])})

        self.assertEqual(list(_adapter_baseline(model)), [Q_PROJ])

    def test_the_snapshot_is_a_copy(self):
        """Training writes through the same tensors; a reference would
        compare them against themselves and find nothing changed."""
        live = Tensor([0.0, 0.0])
        baseline = _adapter_baseline(Model({Q_PROJ: live}))

        live.values[0] = 5.0

        self.assertEqual(baseline[Q_PROJ].values, [0.0, 0.0])


class Comparison(unittest.TestCase):

    def test_a_trained_adapter_reports_movement(self):
        result = verify({Q_PROJ: [0.0, 0.0]}, {Q_PROJ: [0.1, 0.3]})["verification"]

        self.assertEqual(result["changedTensorCount"], 1)
        self.assertTrue(result["valid"])
        self.assertAlmostEqual(
            result["layerModuleDeltaMagnitude"]["0"]["q_proj"], 0.2)

    def test_an_untouched_adapter_is_not_valid(self):
        """The failure mode this exists for: a finished job whose lora_B is
        still the zeros PEFT initialised. It loads; it trained nothing."""
        result = verify({Q_PROJ: [0.0, 0.0]}, {Q_PROJ: [0.0, 0.0]})["verification"]

        self.assertEqual(result["changedTensorCount"], 0)
        self.assertEqual(result["nonZeroTensorCount"], 0)
        self.assertFalse(result["valid"])

    def test_non_lora_parameters_are_ignored(self):
        result = verify({Q_PROJ: [0.0]},
                        {Q_PROJ: [0.5], BASE: [9.0]})["verification"]

        self.assertEqual(result["tensorCount"], 1)

    def test_a_tensor_missing_from_the_baseline_counts_as_changed(self):
        """Nothing to measure the distance from, but it is certainly new."""
        result = verify({}, {Q_PROJ: [0.5]})["verification"]

        self.assertEqual(result["changedTensorCount"], 1)
        self.assertEqual(result["layerModuleDeltaMagnitude"]["0"]["q_proj"], 0.0)


class BadWeights(unittest.TestCase):

    def test_nan_is_counted_and_kept_out_of_the_heatmap(self):
        result = verify({Q_PROJ: [0.0, 0.0]},
                        {Q_PROJ: [float("nan"), 0.1]})["verification"]

        self.assertEqual(result["nanCount"], 1)
        self.assertFalse(result["valid"])
        magnitude = result["layerModuleDeltaMagnitude"]["0"]["q_proj"]
        self.assertTrue(math.isfinite(magnitude),
                        "a NaN magnitude makes json.dumps(allow_nan=False) "
                        "raise and loses the whole metrics file")

    def test_inf_is_counted_and_kept_out_of_the_heatmap(self):
        result = verify({Q_PROJ: [0.0, 0.0]},
                        {Q_PROJ: [float("inf"), 0.1]})["verification"]

        self.assertEqual(result["infCount"], 1)
        self.assertFalse(result["valid"])
        self.assertTrue(math.isfinite(
            result["layerModuleDeltaMagnitude"]["0"]["q_proj"]))

    def test_one_bad_tensor_does_not_hide_a_good_one(self):
        result = verify({Q_PROJ: [0.0], K_PROJ: [0.0]},
                        {Q_PROJ: [float("nan")], K_PROJ: [0.4]})["verification"]

        self.assertEqual(result["tensorCount"], 2)
        self.assertEqual(result["nanCount"], 1)
        self.assertAlmostEqual(
            result["layerModuleDeltaMagnitude"]["0"]["k_proj"], 0.4)


class BaselineFailure(unittest.TestCase):
    """The snapshot runs before the first step, so an exception there costs
    a job that had not even started -- for a chart."""

    def test_a_broken_snapshot_returns_nothing_instead_of_raising(self):
        class Exploding:
            def named_parameters(self):
                raise RuntimeError("cpu out of memory")

        self.assertEqual(_adapter_baseline(Exploding()), {})


class Failure(unittest.TestCase):

    def test_a_broken_read_does_not_fail_the_job(self):
        """The adapter is written and validated by the time this runs. Losing
        a chart is not worth losing a finished job."""
        class Exploding:
            def named_parameters(self):
                raise RuntimeError("cuda gone")

        result = _verify_adapter(Torch(), Exploding(), {})

        self.assertNotIn("verification", result)
        self.assertEqual(result["verificationError"], "RuntimeError")


if __name__ == "__main__":
    unittest.main()
