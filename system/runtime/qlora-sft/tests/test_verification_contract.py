"""The whole training-result verification contract, in one place.

Each earlier ``test_verification_*`` file pins one piece. This one pins what a
reader of ``training_metrics.json`` depends on, end to end:

* a real (tiny) Qwen3 with the runtime's own LoRA settings, trained for a few
  optimizer steps by the real Hugging Face Trainer with the runtime's own
  collator and progress callback, on the CPU -- the runtime itself refuses a
  CPU, so the GPU-only wrappers (``run``, ``_train``) are not called here, but
  every verification function between them is;
* the metrics written by the runtime's own ``_write_metrics``;
* the result directory holding exactly the three files it always did.

The end-to-end class needs torch, transformers and peft and is skipped where
they are missing; the static checks below it run everywhere.
"""

from __future__ import annotations

import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path

from qlora_sft import train, verification
from tests.support import code_only

HAS_STACK = all(importlib.util.find_spec(m) is not None
                for m in ("torch", "transformers", "peft"))

RESULT_FILES = ("adapter_config.json", "adapter_model.safetensors", "training_metrics.json")
EXISTING_METRICS_KEYS = ("trainLoss", "trainingRuntimeSec", "stepsPerSecond",
                         "samplesPerSecond", "peakVramMb", "peakVramReservedMb")
REPO = Path(__file__).resolve().parents[4]


@unittest.skipUnless(HAS_STACK, "torch / transformers / peft not installed")
class TestEndToEnd(unittest.TestCase):
    LAYERS = 3
    STEPS = 6

    @classmethod
    def setUpClass(cls):
        import torch
        from peft import LoraConfig, get_peft_model
        from transformers import (Qwen3Config, Qwen3ForCausalLM, Trainer,
                                  TrainingArguments)

        from qlora_sft.events import EventWriter

        torch.manual_seed(0)
        config = Qwen3Config(vocab_size=64, hidden_size=32, intermediate_size=64,
                             num_hidden_layers=cls.LAYERS, num_attention_heads=4,
                             num_key_value_heads=2, head_dim=8)
        model = get_peft_model(Qwen3ForCausalLM(config), LoraConfig(
            r=train.LORA_R, lora_alpha=train.LORA_ALPHA, lora_dropout=train.LORA_DROPOUT,
            bias="none", task_type="CAUSAL_LM", target_modules=train.LORA_TARGETS))
        cls.targets = set(model.peft_config["default"].target_modules)

        encoded = []
        for i in range(cls.STEPS * 2):
            ids = [(i + j) % 60 + 2 for j in range(8 + i % 3)]
            encoded.append({"input_ids": ids, "labels": [-100] * 3 + ids[3:]})

        baseline = verification.try_snapshot(model)
        cls.baseline_count = len(baseline)
        lines = []
        cls.work = tempfile.TemporaryDirectory()
        trainer = Trainer(
            model=model,
            args=TrainingArguments(
                output_dir=cls.work.name + "/trainer", max_steps=cls.STEPS,
                per_device_train_batch_size=2, learning_rate=1e-2, logging_steps=1,
                save_strategy="no", report_to=[], disable_tqdm=True, use_cpu=True,
                optim="adamw_torch", seed=0, data_seed=0),
            train_dataset=encoded,
            data_collator=train._collate(pad_id=0),
            callbacks=[train.ProgressEvents(EventWriter(lines.append), cls.STEPS)])
        # The console printer is the Trainer's default when tqdm is off; it
        # only prints, and the test output is not the place for it.
        from transformers.trainer_callback import PrinterCallback
        trainer.remove_callback(PrinterCallback)
        result = trainer.train()

        metrics = {"trainLoss": round(float(result.training_loss), 6),
                   **train._loss_curve(trainer)}
        metrics["verification"] = verification.try_verification_report(baseline, model)
        cls.output = Path(cls.work.name) / "output"
        train._save(model, cls.output)
        train._write_metrics(cls.output, metrics)
        cls.metrics = json.loads((cls.output / train.METRICS_FILE).read_text(encoding="utf-8"))
        cls.events = lines

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    # -- loss
    def test_one_loss_point_per_optimizer_step_in_order(self):
        steps = [p["step"] for p in self.metrics["lossHistory"]]

        self.assertEqual(steps, list(range(1, self.STEPS + 1)))

    def test_final_loss_is_the_last_point(self):
        self.assertEqual(self.metrics["finalLoss"], self.metrics["lossHistory"][-1]["loss"])

    def test_train_loss_keeps_its_meaning(self):
        # The Trainer's average, not the last step.
        mean = sum(p["loss"] for p in self.metrics["lossHistory"]) / self.STEPS
        self.assertAlmostEqual(self.metrics["trainLoss"], mean, places=3)

    # -- tensors
    def test_only_lora_was_compared_and_its_count_follows_the_model(self):
        v = self.metrics["verification"]

        self.assertEqual(v["tensorCount"], self.LAYERS * len(self.targets) * 2)
        self.assertEqual(v["tensorCount"], self.baseline_count)

    def test_weights_actually_changed_and_are_finite(self):
        v = self.metrics["verification"]

        self.assertGreater(v["changedTensorCount"], 0)
        self.assertEqual((v["nanCount"], v["infCount"]), (0, 0))
        self.assertIs(v["valid"], True)
        self.assertEqual(v["schemaVersion"], 1)

    def test_non_zero_and_changed_are_different_counts_in_general(self):
        v = self.metrics["verification"]

        # lora_A is non-zero from initialisation; every matrix is non-zero now.
        self.assertEqual(v["nonZeroTensorCount"], v["tensorCount"])
        self.assertLessEqual(v["changedTensorCount"], v["tensorCount"])

    # -- heatmap
    def test_heatmap_covers_every_layer_and_target_module(self):
        cells = self.metrics["verification"]["layerModuleDeltaMagnitude"]

        self.assertEqual(list(cells), [str(i) for i in range(self.LAYERS)])
        for row in cells.values():
            self.assertEqual(set(row), self.targets)
            self.assertTrue(all(value >= 0 for value in row.values()))

    # -- result
    def test_the_result_directory_holds_exactly_the_three_files(self):
        self.assertEqual(sorted(p.name for p in self.output.iterdir()), sorted(RESULT_FILES))

    def test_no_raw_weights_are_in_the_metrics(self):
        text = (self.output / train.METRICS_FILE).read_text(encoding="utf-8")

        self.assertNotIn("lora_A", text)
        self.assertNotIn("base_model", text)
        self.assertLess(len(text), 64 * 1024)

    def test_progress_events_still_carry_every_step(self):
        from qlora_sft.events import EVENT_MARKER, STEP

        payloads = [json.loads(line[len(EVENT_MARKER):]) for line in self.events
                    if line.startswith(EVENT_MARKER)]
        steps = [p["step"] for p in payloads if p.get("event") == STEP]
        self.assertEqual(steps, list(range(1, self.STEPS + 1)))


class TestStaticContract(unittest.TestCase):
    def test_existing_metrics_keys_are_still_produced(self):
        import inspect

        source = inspect.getsource(train._train)
        for key in EXISTING_METRICS_KEYS:
            with self.subTest(key=key):
                self.assertIn(f'"{key}"', source)
        run = inspect.getsource(train.run)
        for key in ("runtimeSec", "sampleCount", "totalSteps", "gpuName",
                    "trainingConfig", "initAdapter"):
            with self.subTest(key=key):
                self.assertIn(f'metrics["{key}"]', run)

    def test_the_worker_packs_the_same_three_files(self):
        artifact = REPO / "system" / "worker" / "gshared_worker" / "result_artifact.py"
        if not artifact.is_file():
            self.skipTest("worker source not beside this runtime")
        block = re.search(r"RESULT_FILES[^=]*=\s*\(([^)]*)\)", artifact.read_text(encoding="utf-8"))

        self.assertEqual(tuple(re.findall(r'"([^"]+)"', block.group(1))), RESULT_FILES)

    def test_metrics_are_still_written_without_nan(self):
        import inspect

        self.assertIn("allow_nan=False", inspect.getsource(train._write_metrics))

    def test_verification_writes_nothing_and_reaches_nothing(self):
        code = code_only(verification)
        for absent in ("open(", "write_text", "json.dump", "requests", "urllib",
                       "socket", "os.environ", "getenv", "save_file", "torch.save"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, code)

    def test_verification_is_not_a_quality_claim(self):
        code = code_only(verification)
        for word in ("accuracy", "quality", "score"):
            with self.subTest(word=word):
                self.assertNotIn(word, code)

    def test_schema_version_is_one(self):
        self.assertEqual(verification.VERIFICATION_SCHEMA_VERSION, 1)


if __name__ == "__main__":
    unittest.main()
