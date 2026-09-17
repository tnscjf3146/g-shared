"""An initial adapter to continue from: what is accepted before the GPU is touched."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from qlora_sft.adapter import (ADAPTER_CONFIG_FILE, ADAPTER_WEIGHTS_FILE,
                               check_adapter_directory)
from qlora_sft.cli import read_request
from qlora_sft.failures import ExitCode, TrainingFailure

from tests.support import code_only

SPEED = ["--epochs", "1", "--batch-size", "16", "--learning-rate", "0.0002",
         "--max-length", "512"]

#: What this runtime itself writes -- read back out of a real job's output.
WRITTEN_CONFIG = {
    "peft_type": "LORA", "task_type": "CAUSAL_LM", "r": 16, "lora_alpha": 32,
    "lora_dropout": 0.05, "base_model_name_or_path": "/job/model",
    "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj",
                       "up_proj", "down_proj"],
}


class AdapterDirectory:
    def __init__(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.path = Path(self._temporary.name)

    def write(self, config=WRITTEN_CONFIG, weights=b"not-empty-weights"):
        if config is not None:
            text = config if isinstance(config, str) else json.dumps(config)
            (self.path / ADAPTER_CONFIG_FILE).write_text(text, encoding="utf-8")
        if weights is not None:
            (self.path / ADAPTER_WEIGHTS_FILE).write_bytes(weights)
        return self

    def cleanup(self):
        self._temporary.cleanup()


class TestTheCommandLine(unittest.TestCase):
    def test_no_adapter_means_a_fresh_one_as_before(self):
        self.assertIsNone(read_request(SPEED).adapter_path)

    def test_the_adapter_path_is_carried(self):
        request = read_request(SPEED + ["--adapter-path", "/job/input/adapter"])

        self.assertEqual(request.adapter_path.as_posix(), "/job/input/adapter")


class TestAnAdapterThisRuntimeWrote(unittest.TestCase):
    def setUp(self):
        self.directory = AdapterDirectory().write()
        self.addCleanup(self.directory.cleanup)

    def test_it_is_accepted(self):
        adapter = check_adapter_directory(self.directory.path)

        self.assertEqual(adapter.config["r"], 16)

    def test_the_loaded_files_are_recorded_by_hash(self):
        described = check_adapter_directory(self.directory.path).describe()

        self.assertEqual(described["weightsSha256"],
                         hashlib.sha256(b"not-empty-weights").hexdigest())
        self.assertEqual(
            described["configSha256"],
            hashlib.sha256((self.directory.path / ADAPTER_CONFIG_FILE)
                           .read_bytes()).hexdigest())
        self.assertEqual(described["r"], 16)
        self.assertEqual(described["loraAlpha"], 32)
        self.assertEqual(described["targetModules"],
                         sorted(WRITTEN_CONFIG["target_modules"]))

    def test_what_is_recorded_carries_no_path(self):
        described = json.dumps(check_adapter_directory(self.directory.path).describe())

        self.assertNotIn(str(self.directory.path), described)
        self.assertNotIn("/job/model", described)

    def test_the_metrics_file_beside_it_is_fine(self):
        # A finished job's result.tar also holds training_metrics.json.
        (self.directory.path / "training_metrics.json").write_text("{}", encoding="utf-8")

        check_adapter_directory(self.directory.path)


class TestWhatIsRefused(unittest.TestCase):
    def refused(self, directory):
        with self.assertRaises(TrainingFailure) as caught:
            check_adapter_directory(directory)
        self.assertEqual(caught.exception.code, ExitCode.INVALID_INPUT)
        return caught.exception.detail

    def make(self, **kwargs):
        directory = AdapterDirectory().write(**kwargs)
        self.addCleanup(directory.cleanup)
        return directory.path

    def test_a_directory_that_is_not_there(self):
        self.refused(Path(tempfile.gettempdir()) / "no-such-adapter-directory")

    def test_either_file_missing(self):
        self.assertIn(ADAPTER_CONFIG_FILE, self.refused(self.make(config=None)))
        self.assertIn(ADAPTER_WEIGHTS_FILE, self.refused(self.make(weights=None)))

    def test_empty_weights(self):
        self.refused(self.make(weights=b""))

    def test_a_config_that_is_not_json_or_not_an_object(self):
        self.refused(self.make(config="{not json"))
        self.refused(self.make(config="[1, 2]"))

    def test_not_lora(self):
        self.refused(self.make(config={**WRITTEN_CONFIG, "peft_type": "IA3"}))

    def test_a_different_task(self):
        self.refused(self.make(config={**WRITTEN_CONFIG, "task_type": "SEQ_CLS"}))

    def test_no_usable_rank(self):
        for rank in (None, 0, -4, "16", True):
            with self.subTest(r=rank):
                self.refused(self.make(config={**WRITTEN_CONFIG, "r": rank}))

    def test_no_target_modules(self):
        for targets in (None, [], [""], 7):
            with self.subTest(target_modules=targets):
                self.refused(self.make(
                    config={**WRITTEN_CONFIG, "target_modules": targets}))

    def test_a_base_model_is_not_an_adapter(self):
        path = self.make()
        (path / "model.safetensors").write_bytes(b"x")

        self.refused(path)

    def test_pickle_weights_are_never_read(self):
        for name in ("adapter_model.bin", "adapter_model.pt"):
            with self.subTest(name=name):
                path = self.make()
                (path / name).write_bytes(b"x")
                self.refused(path)

    def test_a_refusal_does_not_quote_the_path(self):
        path = self.make(config=None)

        self.assertNotIn(str(path), self.refused(path))


class TestTheRunnerUsesIt(unittest.TestCase):
    def test_training_loads_the_adapter_trainable_instead_of_a_fresh_one(self):
        from qlora_sft import train

        code = code_only(train)
        self.assertIn("PeftModel.from_pretrained(model, str(init_adapter.path), "
                      "is_trainable=True)", code)
        self.assertIn("check_adapter_directory(adapter_path)", code)
        self.assertIn("metrics['initAdapter']", code)


if __name__ == "__main__":
    unittest.main()
