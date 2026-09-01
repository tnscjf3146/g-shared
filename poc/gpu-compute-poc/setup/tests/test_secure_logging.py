"""Tests for the Setup event logger's fixed safe event schema."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from gpu_worker_setup.secure_logging import SecureEventLogger


class SecureEventLoggerTests(unittest.TestCase):
    def test_logger_never_writes_token_or_unapproved_fields(self) -> None:
        with TemporaryDirectory() as directory:
            log_path = Path(directory) / "logs" / "setup.log"
            logger = SecureEventLogger(log_path, timestamp_factory=lambda: "2026-09-01T12:00:00Z")

            logger.event(
                "credential_check",
                status="FAILED",
                errorCode="WORKER_TOKEN_REJECTED",
                token="secret-value",
                responseBody="Bearer secret-value",
            )

            text = log_path.read_text(encoding="utf-8")
            record = json.loads(text)
            self.assertNotIn("secret-value", text)
            self.assertNotIn("responseBody", text)
            self.assertEqual(record["event"], "credential_check")
            self.assertEqual(record["status"], "FAILED")
            self.assertEqual(record["errorCode"], "WORKER_TOKEN_REJECTED")

    def test_logger_omits_token_and_bearer_patterns_from_allowlisted_values(self) -> None:
        with TemporaryDirectory() as directory:
            log_path = Path(directory) / "setup.log"
            logger = SecureEventLogger(log_path, timestamp_factory=lambda: "2026-09-01T12:00:00Z")

            logger.event("external_result", status="Bearer abc.def", diagnosticId="token=secret-value")

            text = log_path.read_text(encoding="utf-8")
            record = json.loads(text)
            self.assertNotIn("abc.def", text)
            self.assertNotIn("secret-value", text)
            self.assertNotIn("status", record)
            self.assertNotIn("diagnosticId", record)

    def test_logger_drops_json_and_config_token_forms_even_in_approved_fields_or_event_name(self) -> None:
        with TemporaryDirectory() as directory:
            log_path = Path(directory) / "setup.log"
            logger = SecureEventLogger(log_path, timestamp_factory=lambda: "2026-09-01T12:00:00Z")

            logger.event(
                '{"workerToken":"secret-value"}',
                status='{"token":"secret-value"}',
                diagnosticId="workerToken = secret-value",
            )

            text = log_path.read_text(encoding="utf-8")
            record = json.loads(text)
            self.assertNotIn("secret-value", text)
            self.assertEqual(record["event"], "invalid_event")
            self.assertNotIn("status", record)
            self.assertNotIn("diagnosticId", record)

    def test_logger_retains_only_values_matching_the_safe_field_formats(self) -> None:
        with TemporaryDirectory() as directory:
            log_path = Path(directory) / "setup.log"
            logger = SecureEventLogger(log_path, timestamp_factory=lambda: "2026-09-01T12:00:00Z")

            logger.event(
                "worker_probe",
                runId="7c5a0b2e-4d68-4f03-a07a-28d6d4a2e110",
                diagnosticId="5a5ec04c-6301-4a9a-9fdd-6459f5b9c3cd",
                setupVersion="1.2.3-rc.1+build.7",
                stage="CREDENTIALS_READY",
                status="FAILED",
                errorCode="WORKER_TOKEN_REJECTED",
                safeExitCode=7,
            )

            record = json.loads(log_path.read_text(encoding="utf-8"))
            self.assertEqual(record["runId"], "7c5a0b2e-4d68-4f03-a07a-28d6d4a2e110")
            self.assertEqual(record["diagnosticId"], "5a5ec04c-6301-4a9a-9fdd-6459f5b9c3cd")
            self.assertEqual(record["setupVersion"], "1.2.3-rc.1+build.7")
            self.assertEqual(record["stage"], "CREDENTIALS_READY")
            self.assertEqual(record["status"], "FAILED")
            self.assertEqual(record["errorCode"], "WORKER_TOKEN_REJECTED")
            self.assertEqual(record["safeExitCode"], 7)

    def test_logger_omits_opaque_and_basic_credentials_from_every_string_field(self) -> None:
        with TemporaryDirectory() as directory:
            log_path = Path(directory) / "setup.log"
            logger = SecureEventLogger(log_path, timestamp_factory=lambda: "2026-09-01T12:00:00Z")
            string_fields = (
                "runId",
                "diagnosticId",
                "setupVersion",
                "stage",
                "status",
                "errorCode",
            )

            for field in string_fields:
                for credential in ("secret-value", "Basic dXNlcjpzZWNyZXQ="):
                    with self.subTest(field=field, credential=credential):
                        logger.event("credential_check", **{field: credential})
                        text = log_path.read_text(encoding="utf-8")
                        record = json.loads(text.splitlines()[-1])
                        self.assertNotIn("secret-value", text)
                        self.assertNotIn("dXNlcjpzZWNyZXQ=", text)
                        self.assertNotIn(field, record)

    def test_logger_omits_bool_float_and_out_of_range_safe_exit_codes(self) -> None:
        with TemporaryDirectory() as directory:
            log_path = Path(directory) / "setup.log"
            logger = SecureEventLogger(log_path, timestamp_factory=lambda: "2026-09-01T12:00:00Z")

            for value in (True, 1.5, 2**31):
                with self.subTest(value=value):
                    logger.event("process_exit", safeExitCode=value)
                    record = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
                    self.assertNotIn("safeExitCode", record)

    def test_logger_rotates_before_a_record_exceeds_the_configured_size(self) -> None:
        with TemporaryDirectory() as directory:
            log_path = Path(directory) / "setup.log"
            logger = SecureEventLogger(
                log_path,
                rotation_bytes=80,
                timestamp_factory=lambda: "2026-09-01T12:00:00Z",
            )

            logger.event("first", status="READY")
            logger.event("second", status="READY")

            self.assertTrue(log_path.with_name("setup.log.1").exists())
            self.assertIn('"event":"second"', log_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
