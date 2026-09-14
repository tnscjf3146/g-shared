"""Where the Worker's settings come from, and what happens when they are wrong."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from gshared_worker.config import (
    CONFIG_FILE_NAME,
    DEFAULT_BACKEND_URL,
    DEFAULT_GPU_INDEX,
    DEFAULT_HOURLY_PRICE,
    ENV_BACKEND_URL,
    ENV_GPU_INDEX,
    ENV_HOURLY_PRICE,
    ENV_SUPPORTED_WORKLOADS,
    ENV_WORKER_ID,
    ENV_WORKER_TOKEN,
    WSL_DISTRO_NAME,
    config_file_path,
    read_config_file,
    resolve_settings,
)
from gshared_worker.errors import WorkerConfigurationError

ENROLLED = {
    "backendUrl": "wss://gshared.example/ws/workers",
    "workerId": "worker-001",
    "workerToken": "a-token",
    "hourlyPrice": 900,
    "gpuIndex": 1,
    "supportedWorkloads": ["GPU_PYTORCH_TEST"],
}


class ConfiguredDirectory:
    """A directory holding the Worker, with or without a configuration file."""

    def __init__(self, values=None):
        self._temporary = TemporaryDirectory()
        self.path = Path(self._temporary.name)
        if values is not None:
            self.write(values)

    def write(self, values, *, text=None):
        target = self.path / CONFIG_FILE_NAME
        target.write_text(
            json.dumps(values) if text is None else text, encoding="utf-8"
        )
        return target

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self._temporary.cleanup()
        return False


class TheFileIsTheUsualSourceTest(unittest.TestCase):
    def test_every_setting_is_read_from_it(self):
        with ConfiguredDirectory(ENROLLED) as directory:
            settings = resolve_settings(env={}, directory=directory.path)

        self.assertEqual(settings.backend_url, "wss://gshared.example/ws/workers")
        self.assertEqual(settings.worker_id, "worker-001")
        self.assertEqual(settings.worker_token, "a-token")
        self.assertEqual(settings.hourly_price, 900)
        self.assertEqual(settings.gpu_index, 1)
        self.assertEqual(settings.supported_workloads, ("GPU_PYTORCH_TEST",))

    def test_it_says_which_file_the_values_came_from(self):
        # The run has to be diagnosable from its own output: two config files on
        # one machine is the ordinary case, not the strange one.
        with ConfiguredDirectory(ENROLLED) as directory:
            settings = resolve_settings(env={}, directory=directory.path)

        self.assertEqual(settings.config_path, directory.path / CONFIG_FILE_NAME)

    def test_a_file_that_is_not_there_is_not_an_error(self):
        with ConfiguredDirectory() as directory:
            settings = resolve_settings(
                env={ENV_WORKER_ID: "worker-002"}, directory=directory.path
            )

        self.assertEqual(settings.worker_id, "worker-002")
        self.assertIsNone(settings.config_path)

    def test_a_byte_order_mark_is_not_a_syntax_error(self):
        # Notepad writes one. A supplier editing the file by hand should not be
        # told their file is invalid JSON because of it.
        with ConfiguredDirectory() as directory:
            (directory.path / CONFIG_FILE_NAME).write_text(
                json.dumps(ENROLLED), encoding="utf-8-sig"
            )

            settings = resolve_settings(env={}, directory=directory.path)

        self.assertEqual(settings.worker_id, "worker-001")

    def test_the_config_file_sits_beside_the_worker(self):
        self.assertEqual(
            config_file_path(Path("C:\\G-shared")),
            Path("C:\\G-shared") / CONFIG_FILE_NAME,
        )


class TheEnvironmentWinsTest(unittest.TestCase):
    def test_it_overrides_every_value_in_the_file(self):
        with ConfiguredDirectory(ENROLLED) as directory:
            settings = resolve_settings(
                env={
                    ENV_BACKEND_URL: "ws://localhost:8080/ws/workers",
                    ENV_WORKER_ID: "worker-999",
                    ENV_WORKER_TOKEN: "another-token",
                    ENV_HOURLY_PRICE: "1200",
                    ENV_GPU_INDEX: "3",
                    ENV_SUPPORTED_WORKLOADS: "A, B",
                },
                directory=directory.path,
            )

        self.assertEqual(settings.backend_url, "ws://localhost:8080/ws/workers")
        self.assertEqual(settings.worker_id, "worker-999")
        self.assertEqual(settings.worker_token, "another-token")
        self.assertEqual(settings.hourly_price, 1200)
        self.assertEqual(settings.gpu_index, 3)
        self.assertEqual(settings.supported_workloads, ("A", "B"))

    def test_an_empty_variable_is_not_an_override(self):
        with ConfiguredDirectory(ENROLLED) as directory:
            settings = resolve_settings(
                env={ENV_WORKER_ID: "   "}, directory=directory.path
            )

        self.assertEqual(settings.worker_id, "worker-001")

    def test_a_price_that_is_not_a_number_is_refused_rather_than_ignored(self):
        with ConfiguredDirectory(ENROLLED) as directory:
            with self.assertRaises(WorkerConfigurationError) as refused:
                resolve_settings(
                    env={ENV_HOURLY_PRICE: "free"}, directory=directory.path
                )

        self.assertIn(ENV_HOURLY_PRICE, str(refused.exception))


class WhatTheBuildKnowsOnItsOwnTest(unittest.TestCase):
    def test_a_machine_with_only_a_worker_id_still_has_somewhere_to_connect(self):
        with ConfiguredDirectory() as directory:
            settings = resolve_settings(
                env={ENV_WORKER_ID: "worker-002"}, directory=directory.path
            )

        self.assertEqual(settings.backend_url, DEFAULT_BACKEND_URL)
        self.assertEqual(settings.hourly_price, DEFAULT_HOURLY_PRICE)
        self.assertEqual(settings.gpu_index, DEFAULT_GPU_INDEX)

    def test_the_default_address_is_not_the_poc_deployment(self):
        # The PoC shipped a Railway address of its own. Carrying it across would
        # point a G-shared machine at a service this project does not own.
        self.assertNotIn("railway", DEFAULT_BACKEND_URL)
        self.assertNotIn("poc", DEFAULT_BACKEND_URL)

    def test_a_machine_advertises_nothing_until_the_allowlist_arrives(self):
        # Job execution owns the list of images this machine can actually run.
        # Until it is ported, advertising a name would be advertising work the
        # Worker cannot do.
        with ConfiguredDirectory() as directory:
            settings = resolve_settings(
                env={ENV_WORKER_ID: "worker-002"}, directory=directory.path
            )

        self.assertEqual(settings.supported_workloads, ())

    def test_the_wsl_distribution_is_named_in_one_place(self):
        self.assertEqual(WSL_DISTRO_NAME, "gshared-worker")


class AMachineNobodyConfiguredTest(unittest.TestCase):
    """A first run: no file, no workerId. It is registered later, not refused here.

    The workerId is issued by registration, so requiring it before registration
    meant the executable could never register a machine at all.
    """

    def test_it_is_not_refused_for_having_no_worker_id(self):
        with ConfiguredDirectory() as directory:
            settings = resolve_settings(env={}, directory=directory.path)

        self.assertIsNone(settings.worker_id)
        self.assertIsNone(settings.worker_token)
        self.assertIsNone(settings.config_path)

    def test_it_still_knows_where_to_connect_and_what_it_costs(self):
        with ConfiguredDirectory() as directory:
            settings = resolve_settings(env={}, directory=directory.path)

        self.assertEqual(settings.backend_url, DEFAULT_BACKEND_URL)
        self.assertEqual(settings.hourly_price, DEFAULT_HOURLY_PRICE)


class AFileThatIsThereAndWrongTest(unittest.TestCase):
    def test_invalid_json_names_the_line_and_column(self):
        with ConfiguredDirectory() as directory:
            path = directory.write(None, text="{ not json")

            with self.assertRaises(WorkerConfigurationError) as refused:
                read_config_file(path)

        self.assertIn("not valid JSON", str(refused.exception))

    def test_a_list_at_the_top_is_not_a_configuration(self):
        with ConfiguredDirectory() as directory:
            path = directory.write(None, text="[]")

            with self.assertRaises(WorkerConfigurationError):
                read_config_file(path)

    def test_a_misspelled_key_is_refused_rather_than_silently_ignored(self):
        # Silently ignoring it is a machine running with a setting its owner
        # believes they changed.
        with ConfiguredDirectory() as directory:
            path = directory.write({"workerID": "worker-001"})

            with self.assertRaises(WorkerConfigurationError) as refused:
                read_config_file(path)

        self.assertIn("workerID", str(refused.exception))

    def test_a_comment_key_is_ignored_on_purpose(self):
        with ConfiguredDirectory() as directory:
            path = directory.write({"_comment": "hello", "workerId": "worker-001"})

            self.assertEqual(read_config_file(path), {"workerId": "worker-001"})

    def test_a_price_that_is_text_is_refused(self):
        with ConfiguredDirectory() as directory:
            path = directory.write({"hourlyPrice": "900"})

            with self.assertRaises(WorkerConfigurationError):
                read_config_file(path)

    def test_a_boolean_is_not_an_integer(self):
        with ConfiguredDirectory() as directory:
            path = directory.write({"gpuIndex": True})

            with self.assertRaises(WorkerConfigurationError):
                read_config_file(path)

    def test_workloads_must_be_a_list_of_strings(self):
        with ConfiguredDirectory() as directory:
            path = directory.write({"supportedWorkloads": [1, 2]})

            with self.assertRaises(WorkerConfigurationError):
                read_config_file(path)


class TheStoredTokenTest(unittest.TestCase):
    def test_a_protected_token_is_decrypted_for_this_account(self):
        with ConfiguredDirectory() as directory:
            directory.write({"workerId": "worker-001", "workerTokenProtected": "AAAA"})

            settings = resolve_settings(
                env={},
                directory=directory.path,
                token_unprotector=lambda value: "the-real-token",
            )

        self.assertEqual(settings.worker_token, "the-real-token")

    def test_a_token_that_cannot_be_decrypted_says_only_that(self):
        # What actually failed carries the ciphertext and a Windows error code.
        # Neither belongs on a supplier's screen, and every cause has the same
        # answer: these credentials are not readable on this account.
        def refuses(_value):
            raise ValueError("CryptUnprotectData failed with 0x8009000B on AAAA")

        with ConfiguredDirectory() as directory:
            directory.write({"workerId": "worker-001", "workerTokenProtected": "AAAA"})

            with self.assertRaises(WorkerConfigurationError) as refused:
                resolve_settings(
                    env={}, directory=directory.path, token_unprotector=refuses
                )

        self.assertIn("CREDENTIAL_DECRYPTION_FAILED", str(refused.exception))
        self.assertNotIn("0x8009000B", str(refused.exception))

    def test_the_environment_is_preferred_over_the_protected_file_value(self):
        with ConfiguredDirectory() as directory:
            directory.write({"workerId": "worker-001", "workerTokenProtected": "AAAA"})

            settings = resolve_settings(
                env={ENV_WORKER_TOKEN: "from-the-environment"},
                directory=directory.path,
                token_unprotector=lambda _value: self.fail("must not be decrypted"),
            )

        self.assertEqual(settings.worker_token, "from-the-environment")

    def test_a_machine_with_no_token_at_all_is_not_an_error_here(self):
        # Whether a token is needed is the connection's decision, not this one.
        with ConfiguredDirectory() as directory:
            directory.write({"workerId": "worker-001"})

            settings = resolve_settings(env={}, directory=directory.path)

        self.assertIsNone(settings.worker_token)


if __name__ == "__main__":
    unittest.main()
