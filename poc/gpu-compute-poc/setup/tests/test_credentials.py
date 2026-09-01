"""Tests for credential acquisition and protected Worker config writes."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from gpu_worker.agent_config import DEFAULT_SUPPORTED_WORKLOADS
from gpu_worker_setup.credentials import (
    CredentialInput,
    CredentialMismatchReset,
    ManualCredentialProvider,
    WorkerConfigWriter,
)
from gpu_worker_setup.defaults import DEFAULT_GPU_INDEX, DEFAULT_HOURLY_PRICE
from gpu_worker_setup.endpoint_config import BackendEndpointConfig
from gpu_worker_setup.state_store import STATE_SCHEMA_VERSION, SetupStateStore


class _ReversibleProtector:
    def protect(self, secret: str) -> str:
        return f"protected:{secret[::-1]}"

    def unprotect(self, encoded: str) -> str:
        prefix = "protected:"
        if not encoded.startswith(prefix):
            raise ValueError("invalid protected value")
        return encoded[len(prefix) :][::-1]


class CredentialTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = TemporaryDirectory()
        self.addCleanup(self._temporary_directory.cleanup)
        self.config_path = Path(self._temporary_directory.name) / "worker-config.json"
        self.credentials = CredentialInput(
            backend_url="https://api.example.com/gpu",
            worker_id="worker-001",
            worker_token="credential-value",
        )
        self.endpoints = BackendEndpointConfig.from_base_url(
            self.credentials.backend_url
        )
        self.protector = _ReversibleProtector()

    def test_manual_provider_returns_in_memory_credential_input(self) -> None:
        provider = ManualCredentialProvider(self.credentials)

        self.assertIs(provider.acquire(), self.credentials)

    def test_new_config_contains_only_protected_token_and_worker_defaults(self) -> None:
        writer = WorkerConfigWriter(self.config_path, protector=self.protector)

        writer.write(self.credentials, self.endpoints)

        payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertNotIn("workerToken", payload)
        self.assertNotEqual(
            payload["workerTokenProtected"], self.credentials.worker_token
        )
        self.assertEqual(
            self.protector.unprotect(payload["workerTokenProtected"]),
            self.credentials.worker_token,
        )
        self.assertEqual(payload["backendUrl"], self.endpoints.worker_websocket_url)
        self.assertEqual(payload["workerId"], self.credentials.worker_id)
        self.assertEqual(payload["hourlyPrice"], DEFAULT_HOURLY_PRICE)
        self.assertEqual(payload["gpuIndex"], DEFAULT_GPU_INDEX)
        self.assertEqual(
            payload["supportedWorkloads"], list(DEFAULT_SUPPORTED_WORKLOADS)
        )

    def test_failed_atomic_replace_preserves_existing_config_and_cleans_temp(self) -> None:
        original = b'{"existing":true}'
        self.config_path.write_bytes(original)
        writer = WorkerConfigWriter(self.config_path, protector=self.protector)

        with patch(
            "gpu_worker_setup.state_store.os.replace", side_effect=OSError("blocked")
        ):
            with self.assertRaises(OSError):
                writer.write(self.credentials, self.endpoints)

        self.assertEqual(self.config_path.read_bytes(), original)
        self.assertEqual(list(self.config_path.parent.glob("*.tmp")), [])

    def test_credential_input_rejects_blank_required_values(self) -> None:
        for field in ("backend_url", "worker_id", "worker_token"):
            values = {
                "backend_url": "https://api.example.com",
                "worker_id": "worker-001",
                "worker_token": "credential-value",
            }
            values[field] = "  "
            with self.subTest(field=field), self.assertRaises(ValueError):
                CredentialInput(**values)

    def test_credential_input_rejects_token_with_surrounding_whitespace(self) -> None:
        for value in (" credential-value", "credential-value ", "\tcredential-value"):
            with self.subTest(position=value[:1]), self.assertRaises(ValueError):
                CredentialInput(
                    backend_url="https://api.example.com",
                    worker_id="worker-001",
                    worker_token=value,
                )


class CredentialMismatchResetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = TemporaryDirectory()
        self.addCleanup(self._temporary_directory.cleanup)
        root = Path(self._temporary_directory.name)
        self.config_path = root / "worker-config.json"
        self.state_store = SetupStateStore(root / "setup-state.json")
        self.credentials = CredentialInput(
            backend_url="https://api.example.com/gpu",
            worker_id="worker-001",
            worker_token="credential-value",
        )
        self.endpoints = BackendEndpointConfig.from_base_url(
            self.credentials.backend_url
        )
        WorkerConfigWriter(
            self.config_path, protector=_ReversibleProtector()
        ).write(self.credentials, self.endpoints)
        self.state_store.save(
            {
                "schemaVersion": STATE_SCHEMA_VERSION,
                "completedStages": [
                    "CREDENTIALS_READY",
                    "WSL_READY",
                    "DOCKER_READY",
                    "WORKER_INSTALLED",
                    "BENCHMARK_READY",
                    "REGISTER_VERIFIED",
                    "SETUP_COMPLETED",
                ],
                "workerVersion": "1.2.0",
            }
        )

    def test_mismatch_clears_token_and_completion_but_keeps_url_and_worker_id(self) -> None:
        CredentialMismatchReset(self.config_path, self.state_store).apply()

        payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertNotIn("workerTokenProtected", payload)
        self.assertNotIn("workerToken", payload)
        self.assertEqual(payload["backendUrl"], self.endpoints.worker_websocket_url)
        self.assertEqual(payload["workerId"], self.credentials.worker_id)
        self.assertEqual(payload["hourlyPrice"], DEFAULT_HOURLY_PRICE)

    def test_mismatch_preserves_environment_and_benchmark_stages(self) -> None:
        CredentialMismatchReset(self.config_path, self.state_store).apply()

        state = self.state_store.load()
        self.assertEqual(
            state["completedStages"],
            [
                "CREDENTIALS_READY",
                "WSL_READY",
                "DOCKER_READY",
                "WORKER_INSTALLED",
                "BENCHMARK_READY",
            ],
        )
        self.assertEqual(state["workerVersion"], "1.2.0")

    def test_reset_reports_no_token_so_the_ui_field_returns_blank(self) -> None:
        outcome = CredentialMismatchReset(self.config_path, self.state_store).apply()

        self.assertEqual(outcome.code, "WORKER_CREDENTIAL_MISMATCH")
        self.assertEqual(outcome.backend_url, self.endpoints.worker_websocket_url)
        self.assertEqual(outcome.worker_id, self.credentials.worker_id)
        self.assertEqual(outcome.worker_token, "")

    def test_reset_is_idempotent_and_tolerates_a_missing_config(self) -> None:
        reset = CredentialMismatchReset(self.config_path, self.state_store)
        reset.apply()
        self.config_path.unlink()

        outcome = reset.apply()

        self.assertEqual(outcome.worker_token, "")
        self.assertNotIn("REGISTER_VERIFIED", self.state_store.load()["completedStages"])

    def test_reset_also_removes_a_legacy_plaintext_token(self) -> None:
        self.config_path.write_text(
            json.dumps(
                {
                    "backendUrl": self.endpoints.worker_websocket_url,
                    "workerId": "worker-001",
                    "workerToken": "legacy-plaintext-secret",
                    "hourlyPrice": DEFAULT_HOURLY_PRICE,
                }
            ),
            encoding="utf-8",
        )

        CredentialMismatchReset(self.config_path, self.state_store).apply()

        text = self.config_path.read_text(encoding="utf-8")
        self.assertNotIn("legacy-plaintext-secret", text)
        self.assertEqual(json.loads(text)["workerId"], "worker-001")

    def test_unparseable_config_is_discarded_so_no_token_survives(self) -> None:
        self.config_path.write_text("{not json", encoding="utf-8")

        outcome = CredentialMismatchReset(self.config_path, self.state_store).apply()

        self.assertFalse(self.config_path.exists())
        self.assertIsNone(outcome.backend_url)
        self.assertEqual(outcome.worker_token, "")

    def test_unreadable_config_is_never_deleted(self) -> None:
        reset = CredentialMismatchReset(self.config_path, self.state_store)

        with patch.object(Path, "read_text", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                reset.apply()

        payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertIn("workerTokenProtected", payload)

    def test_failed_state_write_leaves_the_protected_token_in_place(self) -> None:
        reset = CredentialMismatchReset(self.config_path, self.state_store)

        with patch.object(SetupStateStore, "save", side_effect=OSError("blocked")):
            with self.assertRaises(OSError):
                reset.apply()

        payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertIn("workerTokenProtected", payload)
        self.assertEqual(list(self.config_path.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
