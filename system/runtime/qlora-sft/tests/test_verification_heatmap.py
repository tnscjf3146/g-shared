"""Layer x module: names parsed, A and B paired, one number per cell.

Plain Python -- the checks are built by hand. The one test that builds a real
Qwen3 + PEFT model needs torch, transformers and peft, and is skipped where
they are not installed.
"""

from __future__ import annotations

import importlib.util
import json
import unittest

from qlora_sft.verification import TensorCheck, layer_module_delta_magnitude, parse_lora_name

HAS_STACK = all(importlib.util.find_spec(m) is not None
                for m in ("torch", "transformers", "peft"))

PREFIX = "base_model.model.model.layers"


def name(layer, path, module, side):
    return f"{PREFIX}.{layer}.{path}.{module}.lora_{side}.default.weight"


def check(n, delta=1e-4):
    return TensorCheck(name=n, nan_count=0, inf_count=0, non_zero=True,
                       changed=True, delta_mean_abs=delta)


def pair(layer, path, module, a, b):
    return [check(name(layer, path, module, "A"), a), check(name(layer, path, module, "B"), b)]


class TestParsing(unittest.TestCase):
    def test_layer_module_and_side(self):
        self.assertEqual(parse_lora_name(name(0, "self_attn", "q_proj", "A")), (0, "q_proj", "A"))
        self.assertEqual(parse_lora_name(name(27, "mlp", "down_proj", "B")), (27, "down_proj", "B"))

    def test_a_model_without_the_peft_prefix(self):
        self.assertEqual(parse_lora_name("model.layers.3.self_attn.o_proj.lora_B.default.weight"),
                         (3, "o_proj", "B"))

    def test_malformed_names(self):
        for bad in ("", "lm_head.weight", "model.layers.x.self_attn.q_proj.lora_A.default.weight",
                    "model.layers.0.self_attn.q_proj.base_layer.weight",
                    "model.embed_tokens.lora_A.default.weight",
                    "model.layers.0.self_attn.q_proj.lora_C.default.weight"):
            with self.subTest(bad=bad):
                self.assertIsNone(parse_lora_name(bad))


class TestCells(unittest.TestCase):
    def test_a_and_b_are_averaged(self):
        heat = layer_module_delta_magnitude(pair(0, "self_attn", "q_proj", 1e-4, 3e-4))

        self.assertEqual(heat.cells, {"0": {"q_proj": 0.0002}})
        self.assertEqual((heat.unpaired_count, heat.unmapped_count), (0, 0))

    def test_several_layers_and_modules(self):
        checks = []
        for layer in range(3):
            for path, module in (("self_attn", "q_proj"), ("self_attn", "k_proj"), ("mlp", "up_proj")):
                checks += pair(layer, path, module, 1e-4 * (layer + 1), 1e-4 * (layer + 1))

        heat = layer_module_delta_magnitude(checks)

        self.assertEqual(list(heat.cells), ["0", "1", "2"])
        self.assertEqual(list(heat.cells["0"]), ["k_proj", "q_proj", "up_proj"])
        self.assertEqual(heat.cells["2"]["up_proj"], 0.0003)

    def test_layers_are_numeric_order_not_text_order(self):
        checks = pair(10, "mlp", "up_proj", 1e-4, 1e-4) + pair(2, "mlp", "up_proj", 1e-4, 1e-4)

        self.assertEqual(list(layer_module_delta_magnitude(checks).cells), ["2", "10"])

    def test_an_unknown_module_name_does_not_crash_and_gets_a_cell(self):
        heat = layer_module_delta_magnitude(pair(0, "mixer", "in_proj_custom", 1e-4, 1e-4))

        self.assertEqual(heat.cells, {"0": {"in_proj_custom": 0.0001}})

    def test_a_malformed_name_is_counted_not_placed(self):
        heat = layer_module_delta_magnitude(
            pair(0, "self_attn", "q_proj", 1e-4, 1e-4) + [check("model.weird.lora_A.default.weight")])

        self.assertEqual(heat.unmapped_count, 1)
        self.assertEqual(heat.cells, {"0": {"q_proj": 0.0001}})

    def test_a_lone_a_or_b_is_reported_as_unpaired(self):
        checks = pair(0, "self_attn", "q_proj", 1e-4, 1e-4) + [
            check(name(0, "self_attn", "k_proj", "A")), check(name(1, "mlp", "up_proj", "B"))]

        heat = layer_module_delta_magnitude(checks)

        self.assertEqual(heat.unpaired_count, 2)
        self.assertEqual(heat.cells, {"0": {"q_proj": 0.0001}})

    def test_a_non_finite_side_leaves_the_cell_out(self):
        heat = layer_module_delta_magnitude(pair(0, "self_attn", "q_proj", None, 1e-4))

        self.assertEqual(heat.cells, {})
        self.assertEqual(heat.unpaired_count, 0)

    def test_no_raw_parameter_names_reach_the_output(self):
        heat = layer_module_delta_magnitude(pair(0, "self_attn", "q_proj", 1e-4, 1e-4))
        text = json.dumps(heat.cells)

        for fragment in ("lora_", "base_model", "self_attn", ".weight"):
            self.assertNotIn(fragment, text)


@unittest.skipUnless(HAS_STACK, "torch / transformers / peft not installed")
class TestTheRealQwen3Structure(unittest.TestCase):
    """A tiny random Qwen3 with the runtime's own LoRA settings.

    Same architecture class and PEFT call as the runtime, just small: three
    layers so that nothing can quietly depend on 28.
    """

    def test_every_layer_and_every_target_module_gets_a_cell(self):
        import torch
        from peft import LoraConfig, get_peft_model
        from transformers import Qwen3Config, Qwen3ForCausalLM

        from qlora_sft import train
        from qlora_sft.verification import (aggregate, compare_lora_parameters,
                                            snapshot_lora_parameters)

        torch.manual_seed(0)
        config = Qwen3Config(vocab_size=64, hidden_size=32, intermediate_size=64,
                             num_hidden_layers=3, num_attention_heads=4,
                             num_key_value_heads=2, head_dim=8)
        model = get_peft_model(Qwen3ForCausalLM(config), LoraConfig(
            r=train.LORA_R, lora_alpha=train.LORA_ALPHA, lora_dropout=train.LORA_DROPOUT,
            bias="none", task_type="CAUSAL_LM", target_modules=train.LORA_TARGETS))
        targets = set(model.peft_config["default"].target_modules)

        baseline = snapshot_lora_parameters(model)
        with torch.no_grad():
            for n, p in model.named_parameters():
                if ".lora_B." in n:
                    p.add_(1e-3)
        checks = compare_lora_parameters(baseline, model)
        heat = layer_module_delta_magnitude(checks)
        summary = aggregate(checks)

        self.assertEqual(summary["tensorCount"], 3 * len(targets) * 2)
        self.assertEqual(summary["changedTensorCount"], 3 * len(targets))
        self.assertEqual(list(heat.cells), ["0", "1", "2"])
        for layer in heat.cells.values():
            self.assertEqual(set(layer), targets)
        self.assertEqual((heat.unpaired_count, heat.unmapped_count), (0, 0))


if __name__ == "__main__":
    unittest.main()
