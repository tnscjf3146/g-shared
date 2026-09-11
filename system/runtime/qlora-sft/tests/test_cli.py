"""The container's command line, and the step count task 8 will divide by."""

from __future__ import annotations

import unittest

from qlora_sft.cli import (DEFAULT_DATASET_PATH, DEFAULT_MODEL_PATH,
                           DEFAULT_OUTPUT_DIR, read_request, steps_for)
from qlora_sft.config import read_config
from qlora_sft.failures import ExitCode, TrainingFailure

SPEED = ["--epochs", "1", "--batch-size", "16", "--learning-rate", "0.0002",
         "--max-length", "512"]
BALANCED = ["--epochs", "3", "--batch-size", "8", "--learning-rate", "0.0002",
            "--max-length", "1024"]


class TestReadingTheCommandLine(unittest.TestCase):
    def test_the_documented_demo_invocation(self):
        request = read_request(
            ["--model-path", "/job/model",
             "--dataset-path", "/job/input/dataset.jsonl",
             "--output-dir", "/job/output"] + SPEED)

        self.assertEqual(request.model_path.as_posix(), "/job/model")
        self.assertEqual(request.config.epochs, 1)
        self.assertEqual(request.config.batch_size, 16)
        self.assertEqual(request.config.max_length, 512)

    def test_the_paths_default_to_the_mount_contract(self):
        request = read_request(SPEED)

        self.assertEqual(request.model_path.as_posix(), DEFAULT_MODEL_PATH)
        self.assertEqual(request.dataset_path.as_posix(), DEFAULT_DATASET_PATH)
        self.assertEqual(request.output_dir.as_posix(), DEFAULT_OUTPUT_DIR)

    def test_a_seed_can_be_given(self):
        self.assertEqual(read_request(SPEED + ["--seed", "7"]).config.seed, 7)

    def test_there_is_a_default_seed(self):
        # Reproducibility needs *a* seed, not a seed somebody remembered to
        # pass.
        self.assertIsInstance(read_request(SPEED).config.seed, int)

    def test_gradient_checkpointing_is_a_flag(self):
        self.assertTrue(
            read_request(SPEED + ["--gradient-checkpointing"])
            .config.gradient_checkpointing)

    def test_bad_numbers_are_invalid_input_and_not_argparse_noise(self):
        # argparse exits 2 on a malformed command line, which is not one of
        # our codes. A bad --epochs 0 has to land on INVALID_INPUT like every
        # other bad input.
        with self.assertRaises(TrainingFailure) as caught:
            read_request(["--epochs", "0", "--batch-size", "8",
                          "--learning-rate", "0.0002", "--max-length", "512"])

        self.assertEqual(caught.exception.code, ExitCode.INVALID_INPUT)


class TestOneRunnerForBothModels(unittest.TestCase):
    def test_there_is_no_model_switch(self):
        """The difference between the demo and the standard job is the

        weights directory and the numbers -- not a code path. A second path
        per model is a second path to test, and the one that runs less often
        is the one that breaks.
        """
        parser_options = []
        from qlora_sft.cli import build_parser

        for action in build_parser()._actions:
            parser_options.extend(action.option_strings)

        for absent in ("--model", "--model-name", "--profile", "--preset",
                       "--quick-demo", "--standard"):
            self.assertNotIn(absent, parser_options)

    def test_no_module_names_a_model_size(self):
        import inspect

        from qlora_sft import cli, dataset, events, train

        for module in (cli, dataset, events, train):
            with self.subTest(module=module.__name__):
                source = inspect.getsource(module)
                self.assertNotIn("0.6B", source)
                self.assertNotIn("1.7B", source)

    def test_no_runtime_module_hardcodes_a_duration(self):
        # "Qwen3-1.7B takes an hour" is a fact about one GPU and one dataset,
        # and belongs in the documentation that says which GPU.
        import inspect

        from qlora_sft import cli, config, train

        for module in (cli, config, train):
            with self.subTest(module=module.__name__):
                source = inspect.getsource(module)
                for absent in ("45 min", "75 min", "expected_runtime",
                               "ESTIMATED_RUNTIME"):
                    self.assertNotIn(absent, source)


class TestCountingSteps(unittest.TestCase):
    """``progressPercent = currentStep / totalSteps`` needs the denominator

    before the first step rather than after the last one.
    """

    def config(self, **overrides):
        values = {"epochs": 1, "batch_size": 16, "learning_rate": 0.0002,
                  "max_length": 512}
        values.update(overrides)
        return read_config(**values)

    def test_an_exact_fit(self):
        self.assertEqual(steps_for(32, self.config(batch_size=16)), 2)

    def test_a_part_batch_is_still_a_batch(self):
        # Trainer does not drop the remainder, so neither does the count.
        self.assertEqual(steps_for(33, self.config(batch_size=16)), 3)

    def test_epochs_multiply(self):
        self.assertEqual(steps_for(32, self.config(batch_size=16, epochs=3)), 6)

    def test_fewer_samples_than_a_batch_is_one_step(self):
        self.assertEqual(steps_for(3, self.config(batch_size=16)), 1)

    def test_accumulation_makes_steps_rarer(self):
        # batchSize is what goes on the GPU at once; an optimizer step sees
        # accumulation-many of them.
        config = self.config(batch_size=8, gradient_accumulation_steps=4)

        self.assertEqual(steps_for(64, config), 2)

    def test_a_step_cap_wins(self):
        self.assertEqual(steps_for(10000, self.config(batch_size=16),
                                   max_steps=5), 5)

    def test_a_step_cap_above_the_real_count_does_nothing(self):
        self.assertEqual(steps_for(32, self.config(batch_size=16),
                                   max_steps=500), 2)

    def test_the_demo_shape(self):
        # 240 samples, SPEED: one epoch of 15 steps.
        self.assertEqual(steps_for(240, self.config(batch_size=16, epochs=1)), 15)

    def test_the_standard_shape(self):
        # 3000 samples, BALANCED: three epochs of 375 steps.
        self.assertEqual(
            steps_for(3000, self.config(batch_size=8, epochs=3)), 1125)


if __name__ == "__main__":
    unittest.main()
