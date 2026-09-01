"""Tests for allowlisted, atomic Setup state persistence."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from gpu_worker_setup.state_store import SetupStateStore, UpdateJournalStore


class SetupStateStoreTests(unittest.TestCase):
    def test_save_persists_only_the_allowlisted_setup_state(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "setup-state.json"
            store = SetupStateStore(path)
            state = {
                "schemaVersion": 1,
                "completedStages": ["CREDENTIALS_READY", "SYSTEM_READY"],
                "setupVersion": "0.1.0",
                "workerVersion": "1.2.3",
                "lastSafeError": {
                    "stage": "CREDENTIALS_READY",
                    "code": "WORKER_TOKEN_REJECTED",
                    "diagnosticId": "7c5a0b2e-4d68-4f03-a07a-28d6d4a2e110",
                },
                "warnings": ["AUTO_START_FAILED"],
                "dockerLicenseAccepted": True,
                "dockerLicenseAcceptedAt": "2026-09-01T12:00:00Z",
                "autoStartEnabled": True,
            }

            store.save(state)

            self.assertEqual(store.load(), state)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), state)

    def test_save_rejects_secret_or_arbitrary_state_fields(self) -> None:
        with TemporaryDirectory() as directory:
            store = SetupStateStore(Path(directory) / "setup-state.json")

            with self.assertRaises(ValueError):
                store.save({"schemaVersion": 1, "workerToken": "secret-value"})

            self.assertFalse(store.path.exists())

    def test_last_safe_error_accepts_bounded_future_error_stages(self) -> None:
        with TemporaryDirectory() as directory:
            store = SetupStateStore(Path(directory) / "setup-state.json")
            diagnostic_id = "7c5a0b2e-4d68-4f03-a07a-28d6d4a2e110"

            for stage in ("AUTO_START", "UPDATE"):
                with self.subTest(stage=stage):
                    state = {
                        "schemaVersion": 1,
                        "lastSafeError": {
                            "stage": stage,
                            "code": "AUTO_START_FAILED",
                            "diagnosticId": diagnostic_id,
                        },
                    }
                    store.save(state)
                    self.assertEqual(store.load()["lastSafeError"], state["lastSafeError"])

    def test_last_safe_error_rejects_unsafe_or_unbounded_diagnostic_ids(self) -> None:
        with TemporaryDirectory() as directory:
            store = SetupStateStore(Path(directory) / "setup-state.json")

            for diagnostic_id in ("Bearer secret-value", "token=secret-value", "a" * 65):
                with self.subTest(diagnostic_id=diagnostic_id):
                    with self.assertRaises(ValueError):
                        store.save(
                            {
                                "schemaVersion": 1,
                                "lastSafeError": {
                                    "stage": "CREDENTIALS_READY",
                                    "code": "WORKER_TOKEN_REJECTED",
                                    "diagnosticId": diagnostic_id,
                                },
                            }
                        )

    def test_failed_replace_removes_only_its_temp_file_and_preserves_old_state(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "setup-state.json"
            store = SetupStateStore(path)
            old_state = {"schemaVersion": 1, "completedStages": ["SYSTEM_READY"]}
            store.save(old_state)

            with patch("gpu_worker_setup.state_store.os.replace", side_effect=OSError("blocked")):
                with self.assertRaises(OSError):
                    store.save({"schemaVersion": 1, "completedStages": ["DOCKER_READY"]})

            self.assertEqual(store.load(), old_state)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])


class UpdateJournalStoreTests(unittest.TestCase):
    def test_journal_save_load_and_clear_use_a_dedicated_state_path(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "update-transaction.json"
            store = UpdateJournalStore(path)
            journal = {"schemaVersion": 1, "status": "PREPARED", "transactionId": "tx-123"}

            store.save(journal)
            self.assertEqual(store.load(), journal)
            store.clear()

            self.assertIsNone(store.load())
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
