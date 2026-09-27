"""The training numbers, and the models this runtime agrees to load.

The theme running through this file: the runtime checks, and never fixes. A
config that is out of range is refused; it is not clamped, rounded or replaced
with a default. When the mode is CUSTOM those numbers are a customer's own,
and a container that trained something adjacent to what was paid for would be
a worse failure than one that refused to start.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from qlora_sft.config import (CATALOG, MAX_EPOCHS, SUPPORTED_ARCHITECTURE,
                              SUPPORTED_MODEL_TYPE, check_model_directory,
                              read_config)
from qlora_sft.failures import ExitCode, TrainingFailure

GOOD = {"epochs": 3, "batch_size": 8, "learning_rate": 0.0002,
        "max_length": 1024}


def config(**overrides):
    values = dict(GOOD)
    values.update(overrides)
    return read_config(**values)


def model_dir(**overrides) -> Path:
    body = {"model_type": SUPPORTED_MODEL_TYPE,
            "architectures": [SUPPORTED_ARCHITECTURE]}
    body.update(overrides)
    directory = Path(tempfile.mkdtemp())
    (directory / "config.json").write_text(json.dumps(body), encoding="utf-8")
    return directory


class TestTheSchedulersNumbers(unittest.TestCase):
    def test_the_balanced_config_is_taken_as_given(self):
        # The AI Scheduler's BALANCED rule, unchanged on the way through.
        taken = config()

        self.assertEqual(
            (taken.epochs, taken.batch_size, taken.max_length,
             taken.learning_rate), (3, 8, 1024, 0.0002))

    def test_the_speed_config_is_taken_as_given(self):
        taken = config(epochs=1, batch_size=16, max_length=512)

        self.assertEqual((taken.epochs, taken.batch_size, taken.max_length),
                         (1, 16, 512))

    def test_the_training_config_is_taken_as_given(self):
        taken = config(epochs=5, batch_size=4, max_length=2048)

        self.assertEqual((taken.epochs, taken.batch_size, taken.max_length),
                         (5, 4, 2048))

    def test_tokens_per_step_is_the_two_multiplied(self):
        self.assertEqual(config(batch_size=8, max_length=1024).tokens_per_step,
                         8192)

    def test_gradient_checkpointing_is_off_unless_asked_for(self):
        self.assertFalse(config().gradient_checkpointing)


class TestRefusingImpossibleNumbers(unittest.TestCase):
    def refusal(self, **overrides) -> TrainingFailure:
        with self.assertRaises(TrainingFailure) as caught:
            config(**overrides)
        return caught.exception

    def test_zero_epochs(self):
        self.assertEqual(self.refusal(epochs=0).code, ExitCode.INVALID_INPUT)

    def test_negative_epochs(self):
        self.assertIn("epochs", self.refusal(epochs=-1).detail)

    def test_absurd_epochs(self):
        self.assertIn("epochs", self.refusal(epochs=MAX_EPOCHS + 1).detail)

    def test_zero_batch_size(self):
        self.assertIn("batchSize", self.refusal(batch_size=0).detail)

    def test_zero_max_length(self):
        self.assertIn("maxLength", self.refusal(max_length=0).detail)

    def test_a_float_epoch_count(self):
        self.assertIn("whole number", self.refusal(epochs=1.5).detail)

    def test_a_string_batch_size(self):
        self.assertIn("whole number", self.refusal(batch_size="8").detail)

    def test_a_boolean_batch_size(self):
        # True is an int in Python and would pass every range check as a
        # batch size of one -- a different run, not an error, unless this is
        # caught on purpose.
        self.assertIn("whole number", self.refusal(batch_size=True).detail)

    def test_a_boolean_epoch_count(self):
        self.assertIn("whole number", self.refusal(epochs=True).detail)

    def test_a_zero_learning_rate(self):
        self.assertIn("learningRate", self.refusal(learning_rate=0).detail)

    def test_a_negative_learning_rate(self):
        self.assertIn("learningRate", self.refusal(learning_rate=-0.1).detail)

    def test_a_learning_rate_of_one(self):
        self.assertIn("learningRate", self.refusal(learning_rate=1.0).detail)

    def test_a_learning_rate_that_is_not_a_number(self):
        self.assertIn("must be a number",
                      self.refusal(learning_rate="fast").detail)

    def test_a_nan_learning_rate(self):
        # NaN fails every comparison, so the range test catches it only
        # because it is written as `not 0 < rate < 1` rather than as two
        # rejections of out-of-range values.
        self.assertIn("learningRate",
                      self.refusal(learning_rate=float("nan")).detail)

    def test_an_infinite_learning_rate(self):
        self.assertIn("learningRate",
                      self.refusal(learning_rate=float("inf")).detail)

    def test_a_non_boolean_gradient_checkpointing(self):
        self.assertIn("boolean",
                      self.refusal(gradient_checkpointing="yes").detail)

    def test_nothing_is_clamped_into_range(self):
        # The one that matters. A runtime that quietly repaired a config
        # would train something the customer did not ask for and did not pay
        # for, and would report success.
        with self.assertRaises(TrainingFailure):
            config(batch_size=0)
        with self.assertRaises(TrainingFailure):
            config(epochs=10 ** 9)


class TestTheModelWhitelist(unittest.TestCase):
    def test_a_qwen3_directory_is_accepted(self):
        self.assertEqual(check_model_directory(model_dir())["model_type"],
                         SUPPORTED_MODEL_TYPE)

    def test_another_model_family_is_refused(self):
        with self.assertRaises(TrainingFailure) as caught:
            check_model_directory(model_dir(model_type="llama",
                                            architectures=["LlamaForCausalLM"]))

        self.assertEqual(caught.exception.code, ExitCode.MODEL_LOAD_FAILED)

    def test_a_right_type_with_a_wrong_architecture_is_refused(self):
        with self.assertRaises(TrainingFailure) as caught:
            check_model_directory(model_dir(architectures=["Qwen3ForSequenceClassification"]))

        self.assertIn("Qwen3ForCausalLM", caught.exception.detail)

    def test_a_directory_with_no_config_is_refused(self):
        empty = Path(tempfile.mkdtemp())

        with self.assertRaises(TrainingFailure) as caught:
            check_model_directory(empty)

        self.assertIn("config.json", caught.exception.detail)

    def test_an_unreadable_config_is_refused(self):
        directory = Path(tempfile.mkdtemp())
        (directory / "config.json").write_text("{not json", encoding="utf-8")

        with self.assertRaises(TrainingFailure) as caught:
            check_model_directory(directory)

        self.assertEqual(caught.exception.code, ExitCode.MODEL_LOAD_FAILED)

    def test_the_model_is_judged_by_its_own_config(self):
        # Not by a name a caller passed in. There is no argument to this
        # function other than the directory.
        import inspect

        parameters = inspect.signature(check_model_directory).parameters

        self.assertEqual(list(parameters), ["model_path"])


class TestTheCatalogue(unittest.TestCase):
    def test_both_supported_models_are_listed(self):
        self.assertEqual(sorted(CATALOG), ["qwen3-0.6b", "qwen3-1.7b"])

    def test_every_revision_is_a_commit_sha(self):
        # `main` moves. A job priced and scheduled against one set of weights
        # must not train against another set next week under the same name.
        for key, entry in CATALOG.items():
            with self.subTest(model=key):
                self.assertEqual(len(entry.revision), 40, entry.revision)
                self.assertTrue(
                    all(character in "0123456789abcdef"
                        for character in entry.revision))
                self.assertNotIn(entry.revision, ("main", "latest"))

    def test_the_references_are_the_official_repositories(self):
        self.assertEqual(CATALOG["qwen3-0.6b"].ref, "Qwen/Qwen3-0.6B")
        self.assertEqual(CATALOG["qwen3-1.7b"].ref, "Qwen/Qwen3-1.7B")

    def test_the_demo_and_standard_pairings_are_recorded(self):
        self.assertEqual(CATALOG["qwen3-0.6b"].intended_mode, "SPEED")
        self.assertEqual(CATALOG["qwen3-1.7b"].intended_mode, "BALANCED")


if __name__ == "__main__":
    unittest.main()
