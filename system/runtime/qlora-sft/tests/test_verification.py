"""The adapter verification block that ``training_metrics.json`` carries.

A finished job used to prove only that an adapter file existed and could be
loaded back. That is not enough to answer "did this GPU actually train
anything": an adapter of the right shape whose ``lora_B`` is still all zeros
loads perfectly and changes no output. The counts here are what separates the
two, and the per-module magnitudes are what the job detail screen draws.

The arithmetic is kept away from torch on purpose. The caller turns tensors
into :class:`TensorStat` rows; everything below that is plain Python and is
tested without a GPU, without transformers, and without a model load.
"""

from __future__ import annotations

import unittest

from qlora_sft.verification import TensorStat, parse_target, summarize


def stat(name, delta=0.0, changed=False, non_zero=True, nan=False, inf=False):
    return TensorStat(name=name, delta=delta, changed=changed,
                      non_zero=non_zero, has_nan=nan, has_inf=inf)


ATTENTION = ("base_model.model.model.layers.0.self_attn.q_proj"
             ".lora_A.default.weight")
MLP = ("base_model.model.model.layers.11.mlp.gate_proj"
       ".lora_B.default.weight")


class ParseTarget(unittest.TestCase):

    def test_attention_projection(self):
        self.assertEqual(parse_target(ATTENTION), ("0", "q_proj"))

    def test_mlp_projection(self):
        self.assertEqual(parse_target(MLP), ("11", "gate_proj"))

    def test_lora_a_and_b_of_one_module_agree(self):
        """Both halves of a module belong to the same heatmap cell."""
        a = parse_target("base_model.model.model.layers.3.self_attn.v_proj"
                         ".lora_A.default.weight")
        b = parse_target("base_model.model.model.layers.3.self_attn.v_proj"
                         ".lora_B.default.weight")
        self.assertEqual(a, b)
        self.assertEqual(a, ("3", "v_proj"))

    def test_parameter_outside_a_layer(self):
        """Nothing to place on a layer x module grid -- not an error."""
        self.assertIsNone(parse_target("base_model.model.lm_head.weight"))

    def test_non_lora_parameter(self):
        self.assertIsNone(
            parse_target("base_model.model.model.layers.0.self_attn"
                         ".q_proj.base_layer.weight"))


class Counts(unittest.TestCase):

    def test_empty(self):
        """No adapter tensors at all is reported, not crashed on."""
        result = summarize([])
        self.assertEqual(result["tensorCount"], 0)
        self.assertEqual(result["changedTensorCount"], 0)
        self.assertEqual(result["layerModuleDeltaMagnitude"], {})
        # None, not False: "판단할 게 없었다" 와 "학습이 안 됐다" 는 다른 사건이고
        # 화면은 False 를 빨간 "검증 이상 발견" 배지로 그린다
        self.assertIsNone(result["valid"])

    def test_counts_each_category(self):
        result = summarize([
            stat(ATTENTION, delta=0.5, changed=True),
            stat(MLP, delta=0.0, changed=False, non_zero=False),
        ])
        self.assertEqual(result["tensorCount"], 2)
        self.assertEqual(result["changedTensorCount"], 1)
        self.assertEqual(result["nonZeroTensorCount"], 1)
        self.assertEqual(result["nanCount"], 0)
        self.assertEqual(result["infCount"], 0)

    def test_nan_and_inf_are_counted_separately(self):
        result = summarize([
            stat(ATTENTION, changed=True, nan=True),
            stat(MLP, changed=True, inf=True),
        ])
        self.assertEqual(result["nanCount"], 1)
        self.assertEqual(result["infCount"], 1)


class Valid(unittest.TestCase):
    """``valid`` is the one field the screen turns into a word, so it says
    exactly what it means: weights moved, and none of them went bad."""

    def test_true_when_something_changed_and_nothing_is_bad(self):
        self.assertTrue(summarize([stat(ATTENTION, delta=1e-4, changed=True)])
                        ["valid"])

    def test_none_when_there_was_nothing_to_check(self):
        """LoRA tensor 가 하나도 없으면 판단 불가다. False 로 답하면 정상
        완료된 Job 에 "검증 이상 발견" 빨간 배지가 붙는다 —
        trainingResultVerificationPanel 은 null 일 때만 배지를 생략한다."""
        self.assertIsNone(summarize([])["valid"])

    def test_false_when_nothing_changed(self):
        """The failure this whole block exists to catch: a run that finished
        and trained nothing."""
        self.assertFalse(summarize([stat(ATTENTION, changed=False)])["valid"])

    def test_false_on_nan(self):
        self.assertFalse(
            summarize([stat(ATTENTION, changed=True, nan=True)])["valid"])

    def test_false_on_inf(self):
        self.assertFalse(
            summarize([stat(ATTENTION, changed=True, inf=True)])["valid"])


class Heatmap(unittest.TestCase):

    def test_layer_and_module_become_the_grid(self):
        result = summarize([stat(ATTENTION, delta=0.000132, changed=True)])
        self.assertEqual(result["layerModuleDeltaMagnitude"],
                         {"0": {"q_proj": 0.000132}})

    def test_lora_a_and_b_sum_into_one_cell(self):
        """A module is one cell on the screen; it has two tensors."""
        result = summarize([
            stat("base_model.model.model.layers.2.self_attn.k_proj"
                 ".lora_A.default.weight", delta=0.001, changed=True),
            stat("base_model.model.model.layers.2.self_attn.k_proj"
                 ".lora_B.default.weight", delta=0.002, changed=True),
        ])
        self.assertEqual(result["layerModuleDeltaMagnitude"]["2"]["k_proj"],
                         0.003)

    def test_unplaceable_tensor_is_counted_but_not_drawn(self):
        """It is still an adapter tensor; it just has no cell."""
        result = summarize([
            stat("base_model.model.lm_head.lora_A.default.weight",
                 delta=0.9, changed=True),
        ])
        self.assertEqual(result["tensorCount"], 1)
        self.assertEqual(result["layerModuleDeltaMagnitude"], {})

    def test_layers_are_ordered_numerically(self):
        """"10" must not sort before "2".

        The screen does not depend on this -- ``loraWeightHeatmap`` sorts the
        keys itself, and ``_write_metrics`` writes with ``sort_keys=True``
        anyway, which puts them back in string order. It is for whoever opens
        ``training_metrics.json`` to read.
        """
        result = summarize([
            stat("base_model.model.model.layers.10.mlp.up_proj"
                 ".lora_A.default.weight", delta=0.1, changed=True),
            stat("base_model.model.model.layers.2.mlp.up_proj"
                 ".lora_A.default.weight", delta=0.2, changed=True),
        ])
        self.assertEqual(list(result["layerModuleDeltaMagnitude"]), ["2", "10"])


class BackendContract(unittest.TestCase):
    """The names Backend reads out of ``training_metrics.json``.

    This block travels through three services untouched: the runtime writes
    it, the worker packs it into the result tar, and
    ``JobResultVerificationService.parse`` reads it back by name. Nothing
    fails when a name does not match -- the field simply arrives null and the
    screen draws "데이터가 제공되지 않습니다", which is how the first version
    of this shipped with an empty panel.
    """

    EXPECTED = {
        "tensorCount", "changedTensorCount", "nonZeroTensorCount",
        "nanCount", "infCount", "valid", "layerModuleDeltaMagnitude",
    }

    def test_no_field_backend_reads_is_missing(self):
        self.assertEqual(set(summarize([])), self.EXPECTED)

    def test_json_serialisable_with_allow_nan_false(self):
        """``_write_metrics`` writes with ``allow_nan=False``; anything this
        block cannot encode costs the whole file."""
        import json

        json.dumps(summarize([stat(ATTENTION, delta=1e-9, changed=True)]),
                   allow_nan=False)


if __name__ == "__main__":
    unittest.main()
