"""The LoRA baseline: only LoRA, copied, on the CPU.

These need torch, which the host's own test run does not have. They run
wherever torch is installed (the runtime image, a dev venv) and are skipped
otherwise -- the parts of verification that decide the metrics without torch
are covered in the other ``test_verification_*`` files.
"""

from __future__ import annotations

import importlib.util
import unittest

HAS_TORCH = importlib.util.find_spec("torch") is not None


def tiny_lora_model():
    """Two layers shaped like PEFT's naming, with a frozen base weight each."""
    import torch
    from torch import nn

    class Lora(nn.Module):
        def __init__(self):
            super().__init__()
            self.base_layer = nn.Linear(4, 4, bias=False)
            self.base_layer.weight.requires_grad_(False)
            self.lora_A = nn.ModuleDict({"default": nn.Linear(4, 2, bias=False)})
            self.lora_B = nn.ModuleDict({"default": nn.Linear(2, 4, bias=False)})
            nn.init.zeros_(self.lora_B["default"].weight)

    class Attn(nn.Module):
        def __init__(self):
            super().__init__()
            self.q_proj = Lora()
            self.v_proj = Lora()

    class Layer(nn.Module):
        def __init__(self):
            super().__init__()
            self.self_attn = Attn()
            self.norm = nn.LayerNorm(4)
            self.norm.requires_grad_(False)

    class Inner(nn.Module):
        def __init__(self):
            super().__init__()
            self.layers = nn.ModuleList([Layer(), Layer()])
            self.embed_tokens = nn.Embedding(8, 4)
            self.embed_tokens.requires_grad_(False)

    class Model(nn.Module):
        def __init__(self):
            super().__init__()
            self.model = Inner()

    torch.manual_seed(0)
    return Model()


@unittest.skipUnless(HAS_TORCH, "torch is not installed on this interpreter")
class TestTheSnapshot(unittest.TestCase):
    def setUp(self):
        from qlora_sft.verification import snapshot_lora_parameters

        self.model = tiny_lora_model()
        self.snapshot = snapshot_lora_parameters(self.model)

    def test_only_lora_parameters(self):
        self.assertEqual(len(self.snapshot), 2 * 2 * 2)  # layers x modules x (A, B)
        for name in self.snapshot:
            self.assertTrue(".lora_A." in name or ".lora_B." in name, name)

    def test_the_base_model_is_left_out(self):
        for name in self.snapshot:
            self.assertNotIn("base_layer", name)
            self.assertNotIn("embed_tokens", name)
            self.assertNotIn("norm", name)

    def test_both_a_and_b_are_recognised(self):
        self.assertEqual(sum(".lora_A." in n for n in self.snapshot), 4)
        self.assertEqual(sum(".lora_B." in n for n in self.snapshot), 4)

    def test_keys_are_the_parameter_names_and_sorted(self):
        live = {n for n, p in self.model.named_parameters() if p.requires_grad}
        self.assertEqual(set(self.snapshot), live)
        self.assertEqual(list(self.snapshot), sorted(self.snapshot))

    def test_a_later_change_to_the_parameter_does_not_reach_the_snapshot(self):
        import torch

        name = next(iter(self.snapshot))
        before = self.snapshot[name].clone()
        with torch.no_grad():
            dict(self.model.named_parameters())[name].add_(1.0)

        self.assertTrue(torch.equal(self.snapshot[name], before))

    def test_it_is_cpu_float32_and_shares_no_storage(self):
        import torch

        live = dict(self.model.named_parameters())
        for name, copy in self.snapshot.items():
            with self.subTest(name=name):
                self.assertEqual(copy.device.type, "cpu")
                self.assertEqual(copy.dtype, torch.float32)
                self.assertFalse(copy.requires_grad)
                self.assertNotEqual(copy.data_ptr(), live[name].data_ptr())

    def test_half_precision_parameters_are_widened(self):
        import torch

        from qlora_sft.verification import snapshot_lora_parameters

        model = tiny_lora_model().to(torch.bfloat16)
        for copy in snapshot_lora_parameters(model).values():
            self.assertEqual(copy.dtype, torch.float32)

    def test_a_model_without_lora_gives_an_empty_snapshot(self):
        from torch import nn

        from qlora_sft.verification import snapshot_lora_parameters

        self.assertEqual(snapshot_lora_parameters(nn.Linear(3, 3)), {})

    def test_frozen_lora_matrices_are_not_trainable_and_not_taken(self):
        from qlora_sft.verification import snapshot_lora_parameters

        model = tiny_lora_model()
        for name, parameter in model.named_parameters():
            if ".lora_A." in name:
                parameter.requires_grad_(False)

        self.assertTrue(all(".lora_B." in n for n in snapshot_lora_parameters(model)))


class TestTheNameRule(unittest.TestCase):
    """No torch needed: the rule is a name test plus ``requires_grad``."""

    def test_names(self):
        from types import SimpleNamespace

        from qlora_sft.verification import is_lora_parameter

        on, off = SimpleNamespace(requires_grad=True), SimpleNamespace(requires_grad=False)
        name = "base_model.model.model.layers.3.mlp.down_proj.lora_B.default.weight"
        self.assertTrue(is_lora_parameter(name, on))
        self.assertFalse(is_lora_parameter(name, off))
        self.assertFalse(is_lora_parameter(
            "base_model.model.model.layers.3.mlp.down_proj.base_layer.weight", on))
        self.assertFalse(is_lora_parameter("model.lora_Alpha.weight", on))


if __name__ == "__main__":
    unittest.main()
