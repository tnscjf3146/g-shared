"""Before against after: changed is not the same thing as non-zero.

The aggregate is plain Python and runs everywhere. The tensor comparisons need
torch and are skipped on an interpreter without it (see
``test_verification_snapshot``).
"""

from __future__ import annotations

import importlib.util
import unittest

from qlora_sft.verification import TensorCheck, aggregate

HAS_TORCH = importlib.util.find_spec("torch") is not None


@unittest.skipUnless(HAS_TORCH, "torch is not installed on this interpreter")
class TestOneTensor(unittest.TestCase):
    def check(self, before, after):
        import torch

        from qlora_sft.verification import compare_tensor

        return compare_tensor("t", torch.tensor(before, dtype=torch.float32),
                              torch.tensor(after, dtype=torch.float32))

    def test_a_changed_tensor(self):
        c = self.check([0.5, -0.5], [0.5, -0.25])

        self.assertTrue(c.changed and c.non_zero and c.finite)
        self.assertAlmostEqual(c.delta_mean_abs, 0.125)

    def test_an_unchanged_tensor(self):
        c = self.check([0.0, 0.0], [0.0, 0.0])

        self.assertFalse(c.changed)
        self.assertFalse(c.non_zero)
        self.assertEqual(c.delta_mean_abs, 0.0)

    def test_non_zero_but_unchanged_is_not_changed(self):
        # lora_A starts random. Being non-zero proves nothing was learned.
        c = self.check([0.3, -0.7, 0.1], [0.3, -0.7, 0.1])

        self.assertTrue(c.non_zero)
        self.assertFalse(c.changed)
        self.assertEqual(c.delta_mean_abs, 0.0)

    def test_zero_to_non_zero(self):
        # lora_B starts at zero; after a step it is not.
        c = self.check([0.0, 0.0], [0.0, 1e-6])

        self.assertTrue(c.changed and c.non_zero)
        self.assertGreater(c.delta_mean_abs, 0.0)

    def test_a_tiny_update_still_counts_no_epsilon(self):
        c = self.check([1.0], [1.0000001])

        self.assertTrue(c.changed)

    def test_nan(self):
        c = self.check([0.0, 0.0], [float("nan"), 1.0])

        self.assertEqual((c.nan_count, c.inf_count), (1, 0))
        self.assertFalse(c.finite)
        self.assertIsNone(c.delta_mean_abs)

    def test_positive_and_negative_inf(self):
        for value in (float("inf"), float("-inf")):
            with self.subTest(value=value):
                c = self.check([0.0, 0.0], [value, 0.0])
                self.assertEqual((c.nan_count, c.inf_count), (0, 1))
                self.assertIsNone(c.delta_mean_abs)

    def test_bfloat16_after_is_compared_in_float32(self):
        import torch

        from qlora_sft.verification import compare_tensor

        live = torch.tensor([0.5, 0.25], dtype=torch.bfloat16)
        baseline = live.detach().to(device="cpu", dtype=torch.float32, copy=True)

        self.assertFalse(compare_tensor("t", baseline, live).changed)

    def test_a_shape_change_is_refused(self):
        with self.assertRaises(ValueError):
            self.check([0.0, 0.0], [0.0, 0.0, 0.0])


@unittest.skipUnless(HAS_TORCH, "torch is not installed on this interpreter")
class TestAgainstTheModel(unittest.TestCase):
    def test_each_baselined_matrix_is_compared_by_name(self):
        import torch

        from qlora_sft.verification import compare_lora_parameters, snapshot_lora_parameters
        from tests.test_verification_snapshot import tiny_lora_model

        model = tiny_lora_model()
        baseline = snapshot_lora_parameters(model)
        touched = sorted(baseline)[0]
        with torch.no_grad():
            dict(model.named_parameters())[touched].add_(0.01)

        checks = compare_lora_parameters(baseline, model)

        self.assertEqual([c.name for c in checks], sorted(baseline))
        self.assertEqual([c.name for c in checks if c.changed], [touched])

    def test_a_missing_or_new_lora_parameter_is_refused(self):
        from qlora_sft.verification import compare_lora_parameters, snapshot_lora_parameters
        from tests.test_verification_snapshot import tiny_lora_model

        model = tiny_lora_model()
        baseline = snapshot_lora_parameters(model)
        baseline.pop(sorted(baseline)[0])

        with self.assertRaises(ValueError):
            compare_lora_parameters(baseline, model)


def check(name="n", nan=0, inf=0, non_zero=True, changed=True, delta=1e-4):
    return TensorCheck(name=name, nan_count=nan, inf_count=inf, non_zero=non_zero,
                       changed=changed, delta_mean_abs=delta)


class TestAggregate(unittest.TestCase):
    def test_counts_over_several_tensors(self):
        summary = aggregate([
            check("a"), check("b", changed=False), check("c", non_zero=False, changed=False),
            check("d", nan=2, delta=None), check("e", inf=1, delta=None)])

        self.assertEqual(summary, {"tensorCount": 5, "changedTensorCount": 3,
                                   "nonZeroTensorCount": 4, "nanCount": 2, "infCount": 1})

    def test_the_tensor_count_is_whatever_was_compared(self):
        for count in (0, 1, 7, 196, 392, 1000):
            with self.subTest(count=count):
                summary = aggregate([check(str(i)) for i in range(count)])
                self.assertEqual(summary["tensorCount"], count)
                self.assertEqual(summary["changedTensorCount"], count)

    def test_nothing_in_the_module_depends_on_a_fixed_tensor_count(self):
        from qlora_sft import verification
        from tests.support import code_only

        code = code_only(verification)
        for number in ("392", "196", "28"):
            with self.subTest(number=number):
                self.assertNotIn(number, code)


if __name__ == "__main__":
    unittest.main()
