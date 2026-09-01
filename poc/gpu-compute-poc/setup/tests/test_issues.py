"""Tests for the Setup issue value object."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest

from gpu_worker_setup.issues import Retryability, SetupIssue, Severity


class SetupIssueTests(unittest.TestCase):
    def test_issue_carries_the_safe_ui_and_diagnostic_fields(self) -> None:
        issue = SetupIssue(
            stage="CREDENTIALS_READY",
            code="INVALID_BACKEND_URL",
            severity=Severity.BLOCKING,
            retryability=Retryability.USER_ACTION,
            user_message="Enter an HTTPS Backend URL.",
            diagnostic_id="run-123",
        )

        self.assertEqual(issue.code, "INVALID_BACKEND_URL")
        self.assertEqual(issue.diagnostic_id, "run-123")
        with self.assertRaises(FrozenInstanceError):
            issue.code = "CHANGED"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
