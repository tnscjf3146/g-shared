"""What this container is not allowed to do, and what it must refuse.

Three separate promises are checked here, and each of them is the kind that
gets broken by a well-meaning edit six weeks from now rather than on purpose:

* it never learns a credential, because it never talks to anything;
* it never trains on a CPU;
* it is not task 7, task 8 or the blockchain, and contains none of them.

The source searches strip docstrings first. That mistake has been made in this
repository before -- prose explaining that a module never touches a WebSocket
contains the word "WebSocket", and a grep cannot tell the explanation from the
thing being explained.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from qlora_sft import __main__ as entry
from qlora_sft import cli, config, dataset, events, failures, train
from qlora_sft.failures import ERROR_CODES, ExitCode, TrainingFailure
from tests.support import code_only

MODULES = (cli, config, dataset, events, failures, train, entry)


def all_code() -> str:
    return "\n".join(code_only(module) for module in MODULES)


class TestTheContainerLearnsNoSecret(unittest.TestCase):
    """It gets weights, data and numbers. That is the whole of its input."""

    def test_no_token_of_any_kind(self):
        source = all_code()

        for absent in ("workerToken", "worker_token", "accessToken",
                       "access_token", "refresh_token", "Authorization",
                       "Bearer"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)

    def test_no_wallet_or_chain_secret(self):
        source = all_code()

        for absent in ("private_key", "privateKey", "mnemonic", "seed_phrase",
                       "keystore", "web3", "eth_account", "rpc_url"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)

    def test_no_backend_credential(self):
        source = all_code()

        for absent in ("password", "api_key", "apiKey", "secret"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)

    def test_it_reads_no_environment_variable(self):
        # Not even an optional one. Environment is how a credential would
        # arrive without appearing on the command line.
        source = all_code()

        for absent in ("os.environ", "getenv"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)

    def test_the_command_line_asks_only_for_paths_and_numbers(self):
        options = []
        for action in cli.build_parser()._actions:
            options.extend(action.option_strings)

        expected = {"-h", "--help", "--model-path", "--dataset-path",
                    "--output-dir", "--epochs", "--batch-size",
                    "--learning-rate", "--max-length", "--seed",
                    "--gradient-accumulation-steps",
                    "--gradient-checkpointing", "--max-steps"}
        self.assertEqual(set(options), expected)


class TestItTalksToNothing(unittest.TestCase):
    """``--network none`` has to be a fact about the image, not a hope."""

    def test_no_network_client(self):
        source = all_code()

        for absent in ("requests", "urllib", "httpx", "http.client", "socket",
                       "websockets", "aiohttp"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)

    def test_it_never_downloads_a_model(self):
        source = all_code()

        for absent in ("snapshot_download", "hf_hub_download",
                       "from_pretrained(repo", "huggingface_hub"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)

    def test_every_load_is_local_only(self):
        # The flag that turns "fetch it from the hub" into an error.
        source = code_only(train)

        self.assertEqual(source.count("from_pretrained"),
                         source.count("local_files_only=True")
                         + source.count("PeftModel.from_pretrained"))

    def test_the_dev_helper_that_does_download_is_outside_the_image(self):
        # prepare_model.py exists and is allowed to reach the internet. It is
        # not in the package, and the Dockerfile does not copy it.
        here = Path(__file__).resolve().parent.parent

        self.assertTrue((here / "tools" / "prepare_model.py").is_file())
        self.assertFalse((here / "qlora_sft" / "prepare_model.py").exists())
        dockerfile = (here / "Dockerfile").read_text(encoding="utf-8")
        self.assertNotIn("tools", dockerfile)


class TestNoCpuFallback(unittest.TestCase):
    class NoGpu:
        class cuda:
            @staticmethod
            def is_available():
                return False

            @staticmethod
            def device_count():
                return 0

    def test_a_machine_without_cuda_is_a_failure(self):
        with self.assertRaises(TrainingFailure) as caught:
            train._require_gpu(self.NoGpu)

        self.assertEqual(caught.exception.code, ExitCode.CUDA_UNAVAILABLE)

    def test_the_failure_says_it_will_not_use_a_cpu(self):
        with self.assertRaises(TrainingFailure) as caught:
            train._require_gpu(self.NoGpu)

        self.assertIn("does not train on a CPU", caught.exception.detail)

    def test_nothing_moves_a_model_to_the_cpu(self):
        source = code_only(train)

        for absent in ('device_map={"": "cpu"}', 'to("cpu")', "device='cpu'",
                       'device="cpu"', "cpu_offload"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)

    def test_out_of_memory_is_not_retried(self):
        source = code_only(train)

        for absent in ("retry", "attempt", "batch_size //", "batch_size /",
                       "// 2"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)


class TestTheFailureContract(unittest.TestCase):
    def test_every_exit_code_has_a_name(self):
        for code in ExitCode:
            with self.subTest(code=code):
                self.assertIn(code, ERROR_CODES)

    def test_success_is_zero(self):
        self.assertEqual(int(ExitCode.OK), 0)

    def test_every_failure_is_non_zero(self):
        for code in ExitCode:
            if code is not ExitCode.OK:
                self.assertNotEqual(int(code), 0)

    def test_the_documented_codes_are_all_there(self):
        self.assertEqual(
            sorted(name for code, name in ERROR_CODES.items()
                   if code is not ExitCode.OK),
            ["CUDA_OOM", "CUDA_UNAVAILABLE", "INVALID_INPUT",
             "MODEL_LOAD_FAILED", "OUTPUT_VALIDATION_FAILED",
             "TRAINING_FAILED"])

    def test_a_failure_carries_its_code(self):
        failure = TrainingFailure(ExitCode.CUDA_OOM, "no room")

        self.assertEqual(failure.error_code, "CUDA_OOM")

    def test_the_entry_point_turns_a_failure_into_that_exit_code(self):
        lines = []
        code = entry.main(
            ["--epochs", "0", "--batch-size", "8", "--learning-rate", "0.0002",
             "--max-length", "512"],
            events.EventWriter(lines.append))

        self.assertEqual(code, int(ExitCode.INVALID_INPUT))
        self.assertIn("INVALID_INPUT", lines[0])

    def test_the_entry_point_reports_the_failure_as_an_event(self):
        lines = []
        entry.main(["--epochs", "0", "--batch-size", "8",
                    "--learning-rate", "0.0002", "--max-length", "512"],
                   events.EventWriter(lines.append))

        self.assertEqual(len(lines), 1)
        self.assertIn('"event":"failed"', lines[0])


class TestItIsNotTheOtherTasks(unittest.TestCase):
    """Scope, kept by search rather than by memory."""

    def test_no_worker_protocol(self):
        source = all_code()

        for absent in ("JOB_ASSIGN", "JOB_ACCEPTED", "JOB_PROGRESS",
                       "JOB_RESULT", "JOB_FAILED", "JOB_CANCEL", "HEARTBEAT",
                       "REGISTER"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)

    def test_no_blockchain(self):
        source = all_code()

        for absent in ("acceptJob", "submitResult", "abortJob", "escrow",
                       "Escrow", "chain_id", "resultHash"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)

    def test_no_upload_and_no_result_packaging(self):
        source = all_code()

        for absent in ("hashlib", "sha256", "zipfile", "ZipFile", "boto3",
                       "s3", "presigned"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)

    def test_it_does_not_start_containers_of_its_own(self):
        # This code runs *inside* the container. The thing that starts one is
        # task 7 and is not written yet.
        source = all_code()

        for absent in ("subprocess", "Popen", "docker run", "docker ",
                       "nvidia-smi"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, source)


class TestTheAdapterOutputContract(unittest.TestCase):
    def test_the_expected_files_are_the_adapter_ones(self):
        source = code_only(train)

        self.assertIn("adapter_config.json", source)
        self.assertIn("adapter_model.safetensors", source)

    def test_the_base_weights_are_named_as_forbidden_output(self):
        # A job's result is an adapter. Copying the base model into it would
        # hand a customer gigabytes of weights they already had.
        self.assertIn("model.safetensors", train.BASE_WEIGHT_FILES)
        self.assertIn("pytorch_model.bin", train.BASE_WEIGHT_FILES)

    def test_a_missing_adapter_file_fails_validation(self):
        import tempfile

        from qlora_sft.cli import TrainingRequest

        request = TrainingRequest(
            model_path=Path("/nowhere"),
            dataset_path=Path("/nowhere"),
            output_dir=Path(tempfile.mkdtemp()),
            config=config.read_config(epochs=1, batch_size=1,
                                      learning_rate=0.1, max_length=8))

        with self.assertRaises(TrainingFailure) as caught:
            train._check_output(None, request)

        self.assertEqual(caught.exception.code,
                         ExitCode.OUTPUT_VALIDATION_FAILED)

    def test_base_weights_in_the_output_fail_validation(self):
        import tempfile

        from qlora_sft.cli import TrainingRequest

        output = Path(tempfile.mkdtemp())
        (output / "adapter_config.json").write_text("{}", encoding="utf-8")
        (output / "adapter_model.safetensors").write_bytes(b"")
        (output / "model.safetensors").write_bytes(b"")
        request = TrainingRequest(
            model_path=Path("/nowhere"), dataset_path=Path("/nowhere"),
            output_dir=output,
            config=config.read_config(epochs=1, batch_size=1,
                                      learning_rate=0.1, max_length=8))

        with self.assertRaises(TrainingFailure) as caught:
            train._check_output(None, request)

        self.assertIn("base model", caught.exception.detail)


if __name__ == "__main__":
    unittest.main()
