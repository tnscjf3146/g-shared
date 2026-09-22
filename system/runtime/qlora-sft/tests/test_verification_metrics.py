"""``training_metrics.json`` gains ``verification``; nothing it had is lost."""

from __future__ import annotations

import ast
import inspect
import json
import textwrap
import unittest

from qlora_sft.verification import VERIFICATION_SCHEMA_VERSION, TensorCheck, verification_report

PREFIX = "base_model.model.model.layers"


def check(layer, module, side, *, changed=True, nan=0, inf=0, delta=1e-4, non_zero=True):
    return TensorCheck(name=f"{PREFIX}.{layer}.self_attn.{module}.lora_{side}.default.weight",
                       nan_count=nan, inf_count=inf, non_zero=non_zero, changed=changed,
                       delta_mean_abs=delta)


def healthy(layers=2, modules=("q_proj", "v_proj")):
    return [check(layer, module, side, changed=(side == "B"))
            for layer in range(layers) for module in modules for side in "AB"]


class TestTheReport(unittest.TestCase):
    def test_a_healthy_run(self):
        report = verification_report(healthy())

        self.assertEqual(report["schemaVersion"], 1)
        self.assertEqual(VERIFICATION_SCHEMA_VERSION, 1)
        self.assertEqual((report["tensorCount"], report["changedTensorCount"],
                          report["nonZeroTensorCount"]), (8, 4, 8))
        self.assertEqual((report["nanCount"], report["infCount"]), (0, 0))
        self.assertIs(report["valid"], True)
        self.assertEqual(set(report["layerModuleDeltaMagnitude"]), {"0", "1"})

    def test_it_is_json_with_nan_forbidden(self):
        json.dumps(verification_report(healthy()), allow_nan=False)

    def test_no_lora_is_not_valid(self):
        report = verification_report([])

        self.assertIs(report["valid"], False)
        self.assertEqual(report["tensorCount"], 0)
        self.assertEqual(report["layerModuleDeltaMagnitude"], {})

    def test_nothing_changed_is_not_valid(self):
        checks = [check(0, "q_proj", s, changed=False) for s in "AB"]

        self.assertIs(verification_report(checks)["valid"], False)

    def test_nan_or_inf_is_not_valid(self):
        for broken in (check(0, "q_proj", "A", nan=3, delta=None),
                       check(0, "q_proj", "A", inf=1, delta=None)):
            with self.subTest(broken=broken):
                report = verification_report([broken, check(0, "q_proj", "B")])
                self.assertIs(report["valid"], False)
                json.dumps(report, allow_nan=False)

    def test_a_module_missing_half_of_its_pair_is_not_valid(self):
        report = verification_report(healthy() + [check(5, "k_proj", "A")])

        self.assertIs(report["valid"], False)
        self.assertEqual(report["unpairedModuleCount"], 1)

    def test_one_changed_tensor_is_enough(self):
        checks = [check(0, "q_proj", "A", changed=False), check(0, "q_proj", "B", changed=True)]

        self.assertIs(verification_report(checks)["valid"], True)

    def test_the_fields_the_backend_reads(self):
        report = verification_report(healthy())

        for key in ("tensorCount", "changedTensorCount", "nonZeroTensorCount",
                    "nanCount", "infCount", "valid", "layerModuleDeltaMagnitude"):
            self.assertIn(key, report)


class TestTheRunnerOrder(unittest.TestCase):
    """The snapshot is taken after the adapters exist and before training;
    the comparison is made after training. Checked on the source of ``run``,
    which cannot execute here without a GPU."""

    def calls(self):
        from qlora_sft import train

        tree = ast.parse(textwrap.dedent(inspect.getsource(train.run)))
        names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                names.append((node.lineno, node.col_offset, node.func.id))
        return [name for _, _, name in sorted(names)]

    def test_snapshot_then_train_then_compare(self):
        calls = self.calls()

        self.assertLess(calls.index("_attach_adapters"), calls.index("try_snapshot"))
        self.assertLess(calls.index("try_snapshot"), calls.index("_train"))
        self.assertLess(calls.index("_train"), calls.index("try_verification_report"))
        self.assertLess(calls.index("try_verification_report"), calls.index("_write_metrics"))

    def test_the_metrics_key_is_verification(self):
        from qlora_sft import train

        self.assertIn('metrics["verification"]', inspect.getsource(train.run))


if __name__ == "__main__":
    unittest.main()
