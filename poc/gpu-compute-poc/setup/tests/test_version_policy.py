"""Tests for semantic Worker versions and local update policy."""

from __future__ import annotations

import unittest

from gpu_worker_setup.version_policy import (
    LocalVersionPolicy,
    RollbackContext,
    VersionMetadata,
    WorkerVersion,
)


def v(value: str) -> WorkerVersion:
    return WorkerVersion.parse(value)


class WorkerVersionTests(unittest.TestCase):
    def test_semver_prereleases_sort_before_the_associated_release(self) -> None:
        self.assertLess(v("1.2.0-alpha.1"), v("1.2.0-alpha.beta"))
        self.assertLess(v("1.2.0-rc.1"), v("1.2.0"))
        self.assertEqual(v("1.2.0+build.1"), v("1.2.0+build.2"))
        self.assertEqual(hash(v("1.2.0+build.1")), hash(v("1.2.0+build.2")))

    def test_parse_rejects_non_semantic_versions(self) -> None:
        for value in ("1.2", "v1.2.3", "1.2.3-01", "1.2.3+", "1٢.2.3"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    WorkerVersion.parse(value)


class LocalVersionPolicyTests(unittest.TestCase):
    def test_arbitrary_downgrade_is_blocked_but_exact_journal_rollback_is_allowed(self) -> None:
        policy = LocalVersionPolicy()
        self.assertEqual(
            policy.evaluate(v("1.2.0"), v("1.1.0"), VersionMetadata()).reason_code,
            "WORKER_DOWNGRADE_BLOCKED",
        )
        rollback = RollbackContext(
            previous_version=v("1.1.0"), previous_manifest_sha256="a" * 64
        )
        self.assertTrue(
            policy.evaluate(v("1.2.0"), v("1.1.0"), VersionMetadata(), rollback).allowed
        )

    def test_minimum_policy_marks_an_old_current_worker_unsupported(self) -> None:
        decision = LocalVersionPolicy().evaluate(
            v("1.0.0"),
            v("1.2.0"),
            VersionMetadata(minimum_supported_worker_version=v("1.1.0")),
        )

        self.assertFalse(decision.receive_work_allowed)
        self.assertTrue(decision.update_required)
        self.assertEqual(decision.reason_code, "WORKER_VERSION_UNSUPPORTED")

    def test_minimum_policy_keeps_an_arbitrary_downgrade_blocked(self) -> None:
        decision = LocalVersionPolicy().evaluate(
            v("1.0.0"),
            v("0.9.0"),
            VersionMetadata(minimum_supported_worker_version=v("1.1.0")),
        )

        self.assertFalse(decision.allowed)
        self.assertFalse(decision.receive_work_allowed)
        self.assertTrue(decision.update_required)
        self.assertEqual(decision.reason_code, "WORKER_DOWNGRADE_BLOCKED")

    def test_rollback_requires_exact_version_identity_including_build_metadata(self) -> None:
        decision = LocalVersionPolicy().evaluate(
            v("1.2.0"),
            v("1.1.0+different"),
            VersionMetadata(),
            RollbackContext(
                previous_version=v("1.1.0+known"),
                previous_manifest_sha256="a" * 64,
            ),
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, "WORKER_DOWNGRADE_BLOCKED")

    def test_recommended_policy_does_not_require_an_update(self) -> None:
        decision = LocalVersionPolicy().evaluate(
            v("1.1.0"),
            v("1.2.0"),
            VersionMetadata(recommended_worker_version=v("1.2.0")),
        )

        self.assertTrue(decision.receive_work_allowed)
        self.assertFalse(decision.update_required)
        self.assertTrue(decision.update_recommended)
        self.assertEqual(decision.reason_code, "WORKER_UPDATE_RECOMMENDED")

    def test_rollback_context_requires_a_sha256_manifest_hash(self) -> None:
        with self.assertRaises(ValueError):
            RollbackContext(previous_version=v("1.1.0"), previous_manifest_sha256="bad")


if __name__ == "__main__":
    unittest.main()
