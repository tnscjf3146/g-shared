"""Tests for executable-directory agent configuration discovery and merging."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from gpu_worker.agent_config import (
    CONFIG_FILE_NAME,
    agent_directory,
    config_file_path,
    read_config_file,
    resolve_settings,
)
from gpu_worker.errors import WorkerConfigurationError


def _args(**overrides) -> Namespace:
    values = {
        "backend_url": None,
        "worker_id": None,
        "hourly_price": None,
        "gpu_index": None,
        "supported_workloads": None,
    }
    values.update(overrides)
    return Namespace(**values)


def _write(directory: Path, payload: object) -> Path:
    path = directory / CONFIG_FILE_NAME
    text = payload if isinstance(payload, str) else json.dumps(payload)
    path.write_text(text, encoding="utf-8")
    return path


class AgentDirectoryTest(unittest.TestCase):
    def test_frozen_agent_directory_follows_executable_not_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "gpu-worker-agent.exe"
            executable.write_bytes(b"")
            with patch.object(sys, "executable", str(executable)), patch.object(
                sys, "frozen", True, create=True
            ):
                self.assertEqual(agent_directory(), Path(tmp).resolve())

    def test_frozen_agent_directory_ignores_meipass_extraction_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "gpu-worker-agent.exe"
            executable.write_bytes(b"")
            with patch.object(sys, "executable", str(executable)), patch.object(
                sys, "frozen", True, create=True
            ), patch.object(sys, "_MEIPASS", tmp + "-extracted", create=True):
                self.assertEqual(agent_directory(), Path(tmp).resolve())

    def test_source_agent_directory_is_worker_package_root(self) -> None:
        import gpu_worker

        expected = Path(gpu_worker.__file__).resolve().parent.parent
        self.assertEqual(agent_directory(), expected)

    def test_config_file_path_uses_given_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                config_file_path(Path(tmp)), Path(tmp) / "worker-config.json"
            )


class ReadConfigFileTest(unittest.TestCase):
    def test_example_advertises_both_fixed_gpu_workloads(self) -> None:
        example = json.loads(
            (Path(__file__).resolve().parents[1] / "worker-config.example.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(
            example["supportedWorkloads"],
            ["GPU_PYTORCH_TEST", "GPU_PYTORCH_TEST_30S"],
        )

    def test_missing_file_reads_as_empty_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(read_config_file(config_file_path(Path(tmp))), {})

    def test_malformed_json_reports_file_path_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(Path(tmp), "{not json")
            with self.assertRaises(WorkerConfigurationError) as caught:
                read_config_file(path)
            message = str(caught.exception)
            self.assertIn(CONFIG_FILE_NAME, message)
            self.assertIn("valid JSON", message)

    def test_non_object_json_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(Path(tmp), ["backendUrl"])
            with self.assertRaisesRegex(WorkerConfigurationError, "JSON object"):
                read_config_file(path)

    def test_unknown_keys_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(Path(tmp), {"workerid": "worker-001"})
            with self.assertRaisesRegex(WorkerConfigurationError, "workerid"):
                read_config_file(path)

    def test_comment_keys_are_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(Path(tmp), {"_comment": "example", "workerId": "worker-001"})
            self.assertEqual(read_config_file(path), {"workerId": "worker-001"})

    def test_wrong_value_types_are_rejected(self) -> None:
        cases = (
            {"backendUrl": 1},
            {"workerId": []},
            {"workerToken": 5},
            {"hourlyPrice": "600"},
            {"hourlyPrice": True},
            {"gpuIndex": "0"},
            {"supportedWorkloads": "GPU_PYTORCH_TEST"},
            {"supportedWorkloads": [1]},
        )
        for payload in cases:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as tmp:
                path = _write(Path(tmp), payload)
                with self.assertRaises(WorkerConfigurationError):
                    read_config_file(path)


class ResolveSettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.directory = Path(self._tmp.name)

    def test_config_file_alone_produces_complete_settings(self) -> None:
        _write(
            self.directory,
            {
                "backendUrl": "wss://example.com/ws/workers",
                "workerId": "worker-001",
                "workerToken": "file-token",
                "hourlyPrice": 600,
                "gpuIndex": 1,
                "supportedWorkloads": ["GPU_PYTORCH_TEST"],
            },
        )

        settings = resolve_settings(_args(), env={}, directory=self.directory)

        self.assertEqual(settings.backend_url, "wss://example.com/ws/workers")
        self.assertEqual(settings.worker_id, "worker-001")
        self.assertEqual(settings.worker_token, "file-token")
        self.assertEqual(settings.hourly_price, 600)
        self.assertEqual(settings.gpu_index, 1)
        self.assertEqual(settings.supported_workloads, ("GPU_PYTORCH_TEST",))
        self.assertEqual(settings.config_path, config_file_path(self.directory))

    def test_defaults_apply_when_optional_values_are_absent(self) -> None:
        _write(
            self.directory,
            {
                "backendUrl": "wss://example.com/ws/workers",
                "workerId": "worker-001",
                "hourlyPrice": 600,
            },
        )

        settings = resolve_settings(_args(), env={}, directory=self.directory)

        self.assertEqual(settings.gpu_index, 0)
        self.assertEqual(settings.supported_workloads, ("EMBEDDING",))
        self.assertIsNone(settings.worker_token)

    def test_cli_arguments_beat_environment_and_config_file(self) -> None:
        _write(
            self.directory,
            {
                "backendUrl": "wss://file/ws/workers",
                "workerId": "file-worker",
                "hourlyPrice": 100,
                "gpuIndex": 3,
                "supportedWorkloads": ["EMBEDDING"],
            },
        )
        env = {
            "GPU_WORKER_BACKEND_URL": "wss://env/ws/workers",
            "GPU_WORKER_ID": "env-worker",
            "GPU_WORKER_HOURLY_PRICE": "200",
            "GPU_WORKER_GPU_INDEX": "2",
            "GPU_WORKER_SUPPORTED_WORKLOADS": "EMBEDDING",
        }

        settings = resolve_settings(
            _args(
                backend_url="wss://cli/ws/workers",
                worker_id="cli-worker",
                hourly_price=300,
                gpu_index=1,
                supported_workloads=["GPU_PYTORCH_TEST"],
            ),
            env=env,
            directory=self.directory,
        )

        self.assertEqual(settings.backend_url, "wss://cli/ws/workers")
        self.assertEqual(settings.worker_id, "cli-worker")
        self.assertEqual(settings.hourly_price, 300)
        self.assertEqual(settings.gpu_index, 1)
        self.assertEqual(settings.supported_workloads, ("GPU_PYTORCH_TEST",))

    def test_environment_beats_config_file(self) -> None:
        _write(
            self.directory,
            {
                "backendUrl": "wss://file/ws/workers",
                "workerId": "file-worker",
                "hourlyPrice": 100,
                "gpuIndex": 3,
                "supportedWorkloads": ["EMBEDDING"],
            },
        )
        env = {
            "GPU_WORKER_BACKEND_URL": "wss://env/ws/workers",
            "GPU_WORKER_ID": "env-worker",
            "GPU_WORKER_HOURLY_PRICE": "200",
            "GPU_WORKER_GPU_INDEX": "2",
            "GPU_WORKER_SUPPORTED_WORKLOADS": "GPU_PYTORCH_TEST, EMBEDDING",
        }

        settings = resolve_settings(_args(), env=env, directory=self.directory)

        self.assertEqual(settings.backend_url, "wss://env/ws/workers")
        self.assertEqual(settings.worker_id, "env-worker")
        self.assertEqual(settings.hourly_price, 200)
        self.assertEqual(settings.gpu_index, 2)
        self.assertEqual(
            settings.supported_workloads, ("GPU_PYTORCH_TEST", "EMBEDDING")
        )

    def test_token_environment_variable_beats_config_file(self) -> None:
        _write(
            self.directory,
            {
                "backendUrl": "wss://example.com/ws/workers",
                "workerId": "worker-001",
                "workerToken": "file-token",
                "hourlyPrice": 600,
            },
        )

        settings = resolve_settings(
            _args(), env={"GPU_WORKER_TOKEN": "env-token"}, directory=self.directory
        )

        self.assertEqual(settings.worker_token, "env-token")

    def test_protected_config_precedes_legacy_plaintext(self) -> None:
        _write(
            self.directory,
            {
                "backendUrl": "wss://example.com/ws/workers",
                "workerId": "worker-001",
                "workerTokenProtected": "encoded-value",
                "workerToken": "legacy-value",
                "hourlyPrice": 600,
            },
        )

        settings = resolve_settings(
            _args(),
            env={},
            directory=self.directory,
            token_unprotector=lambda value: "protected-value"
            if value == "encoded-value"
            else None,
        )

        self.assertEqual(settings.worker_token, "protected-value")

    def test_broken_protected_config_never_falls_back_to_legacy_plaintext(self) -> None:
        _write(
            self.directory,
            {
                "backendUrl": "wss://example.com/ws/workers",
                "workerId": "worker-001",
                "workerTokenProtected": "broken",
                "workerToken": "legacy-value",
                "hourlyPrice": 600,
            },
        )

        def fail_unprotect(_value: str) -> str:
            raise ValueError("unsafe dependency detail")

        with self.assertRaisesRegex(
            WorkerConfigurationError, "CREDENTIAL_DECRYPTION_FAILED"
        ) as caught:
            resolve_settings(
                _args(),
                env={},
                directory=self.directory,
                token_unprotector=fail_unprotect,
            )

        self.assertNotIn("unsafe dependency detail", str(caught.exception))
        self.assertNotIn("legacy-value", str(caught.exception))

    def test_non_string_protected_config_uses_safe_failure_without_legacy_fallback(
        self,
    ) -> None:
        for legacy in (False, True):
            payload = {
                "backendUrl": "wss://example.com/ws/workers",
                "workerId": "worker-001",
                "workerTokenProtected": 5,
                "hourlyPrice": 600,
            }
            if legacy:
                payload["workerToken"] = "legacy-value"
            _write(self.directory, payload)

            with self.subTest(legacy=legacy), self.assertRaisesRegex(
                WorkerConfigurationError, "CREDENTIAL_DECRYPTION_FAILED"
            ) as caught:
                resolve_settings(_args(), env={}, directory=self.directory)

            self.assertNotIn("must be a string", str(caught.exception))
            self.assertNotIn("legacy-value", str(caught.exception))

    def test_environment_override_bypasses_non_string_protected_config(self) -> None:
        _write(
            self.directory,
            {
                "backendUrl": "wss://example.com/ws/workers",
                "workerId": "worker-001",
                "workerTokenProtected": 5,
                "workerToken": "legacy-value",
                "hourlyPrice": 600,
            },
        )

        settings = resolve_settings(
            _args(),
            env={"GPU_WORKER_TOKEN": "environment-value"},
            directory=self.directory,
        )

        self.assertEqual(settings.worker_token, "environment-value")

    def test_empty_unprotected_config_is_a_safe_decryption_failure(self) -> None:
        _write(
            self.directory,
            {
                "backendUrl": "wss://example.com/ws/workers",
                "workerId": "worker-001",
                "workerTokenProtected": "encoded-value",
                "hourlyPrice": 600,
            },
        )

        with self.assertRaisesRegex(
            WorkerConfigurationError, "CREDENTIAL_DECRYPTION_FAILED"
        ):
            resolve_settings(
                _args(),
                env={},
                directory=self.directory,
                token_unprotector=lambda _value: "  ",
            )

    def test_environment_override_does_not_attempt_protected_decryption(self) -> None:
        _write(
            self.directory,
            {
                "backendUrl": "wss://example.com/ws/workers",
                "workerId": "worker-001",
                "workerTokenProtected": "broken",
                "workerToken": "legacy-value",
                "hourlyPrice": 600,
            },
        )

        def unexpected_unprotect(_value: str) -> str:
            self.fail("protected config must not be read when the environment wins")

        settings = resolve_settings(
            _args(),
            env={"GPU_WORKER_TOKEN": "environment-value"},
            directory=self.directory,
            token_unprotector=unexpected_unprotect,
        )

        self.assertEqual(settings.worker_token, "environment-value")

    def test_missing_config_file_and_arguments_names_file_and_example(self) -> None:
        with self.assertRaises(WorkerConfigurationError) as caught:
            resolve_settings(_args(), env={}, directory=self.directory)

        message = str(caught.exception)
        self.assertIn(str(config_file_path(self.directory)), message)
        self.assertIn("worker-config.example.json", message)
        self.assertNotIn("Traceback", message)

    def test_incomplete_configuration_lists_missing_keys(self) -> None:
        _write(self.directory, {"backendUrl": "wss://example.com/ws/workers"})

        with self.assertRaises(WorkerConfigurationError) as caught:
            resolve_settings(_args(), env={}, directory=self.directory)

        message = str(caught.exception)
        self.assertIn("workerId", message)
        self.assertIn("hourlyPrice", message)
        self.assertNotIn("backendUrl", message)

    def test_invalid_environment_integer_is_readable(self) -> None:
        env = {
            "GPU_WORKER_BACKEND_URL": "wss://env/ws/workers",
            "GPU_WORKER_ID": "env-worker",
            "GPU_WORKER_HOURLY_PRICE": "not-a-number",
        }

        with self.assertRaisesRegex(
            WorkerConfigurationError, "GPU_WORKER_HOURLY_PRICE"
        ):
            resolve_settings(_args(), env=env, directory=self.directory)

    def test_cli_only_configuration_needs_no_config_file(self) -> None:
        settings = resolve_settings(
            _args(
                backend_url="ws://127.0.0.1:8080/ws/workers",
                worker_id="worker-001",
                hourly_price=600,
            ),
            env={},
            directory=self.directory,
        )

        self.assertEqual(settings.worker_id, "worker-001")
        self.assertIsNone(settings.config_path)


if __name__ == "__main__":
    unittest.main()
