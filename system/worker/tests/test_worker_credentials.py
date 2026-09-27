"""The Worker's identity on disk: written sealed, read back, and how it breaks.

The distinction this file exists to protect: **no credentials** and **broken
credentials** are not the same thing. Treating the second as the first is how a
Worker silently replaces an identity somebody deliberately put there.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from gshared_worker.config import CONFIG_FILE_NAME
from gshared_worker.worker_credentials import (
    CredentialState,
    load_credentials,
    store_credentials,
)

SEALED = "sealed:"


def seal(secret: str) -> str:
    return SEALED + secret


def unseal(sealed: str) -> str:
    if not sealed.startswith(SEALED):
        raise ValueError("this was not protected for this account")
    return sealed[len(SEALED):]


class CredentialTestCase(unittest.TestCase):
    def setUp(self):
        self._temporary = TemporaryDirectory()
        self.directory = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)

    @property
    def path(self) -> Path:
        return self.directory / CONFIG_FILE_NAME

    def write(self, values):
        self.path.write_text(json.dumps(values), encoding="utf-8")

    def store(self, worker_id="worker-001", token="secret-token"):
        return store_credentials(worker_id, token, self.directory, protector=seal)

    def load(self, unprotector=unseal):
        return load_credentials(self.directory, unprotector=unprotector)


class StoringAnIdentityTest(CredentialTestCase):
    def test_it_writes_the_keys_the_reader_already_looks_for(self):
        # Same file, same names as the settings the Worker already reads.
        self.store()

        written = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(written["workerId"], "worker-001")
        self.assertIn("workerTokenProtected", written)

    def test_the_token_goes_through_the_protector_before_it_is_written(self):
        # That the sealed value hides the token is DPAPI's job, checked against
        # the real thing in ThroughRealWindowsDpapiTest. What is checked here
        # is that nothing writes the token without asking the protector first.
        self.store(token="a-very-secret-token")

        written = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(written["workerTokenProtected"], seal("a-very-secret-token"))
        self.assertNotIn("workerToken", written)

    def test_a_plaintext_token_from_an_earlier_version_is_removed(self):
        # Otherwise it survives beside the protected one and is what leaks.
        self.write({"workerToken": "left-over-secret", "hourlyPrice": 600})

        self.store()

        written = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertNotIn("workerToken", written)
        self.assertNotIn("left-over-secret", self.path.read_text(encoding="utf-8"))

    def test_other_settings_are_kept(self):
        # A supplier may have set a price. Being registered is no reason to
        # lose it.
        self.write({"hourlyPrice": 900, "gpuIndex": 1})

        self.store()

        written = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(written["hourlyPrice"], 900)
        self.assertEqual(written["gpuIndex"], 1)

    def test_an_unreadable_file_is_replaced_rather_than_merged(self):
        self.path.write_text("{ not json", encoding="utf-8")

        self.store()

        self.assertEqual(self.load().credentials.worker_id, "worker-001")

    def test_half_an_identity_is_never_written(self):
        for worker_id, token in (("", "t"), ("w", ""), ("  ", "t")):
            with self.assertRaises(ValueError):
                store_credentials(worker_id, token, self.directory, protector=seal)

    def test_the_file_is_moved_into_place_rather_than_written_over(self):
        # An interrupted write must not leave half an identity where a whole
        # one was.
        self.store(worker_id="worker-001")
        self.store(worker_id="worker-002")

        self.assertEqual(self.load().credentials.worker_id, "worker-002")
        self.assertFalse(list(self.directory.glob("*.new")))


class ReadingItBackTest(CredentialTestCase):
    def test_a_stored_identity_comes_back_whole(self):
        self.store(worker_id="worker-007", token="the-token")

        loaded = self.load()

        self.assertIs(loaded.state, CredentialState.PRESENT)
        self.assertTrue(loaded.is_present)
        self.assertEqual(loaded.credentials.worker_id, "worker-007")
        self.assertEqual(loaded.credentials.worker_token, "the-token")

    def test_what_is_reported_never_carries_the_token(self):
        self.store(token="the-token")

        self.assertNotIn("the-token", self.load().detail)


class NoCredentialsIsNotBrokenCredentialsTest(CredentialTestCase):
    def test_no_file_at_all_is_absent(self):
        self.assertIs(self.load().state, CredentialState.ABSENT)

    def test_a_settings_file_with_no_identity_is_absent(self):
        # Somebody set a price before ever registering.
        self.write({"hourlyPrice": 600})

        self.assertIs(self.load().state, CredentialState.ABSENT)

    def test_a_token_that_cannot_be_decrypted_is_unusable_not_absent(self):
        # Copied here from another Windows account. Registering again would
        # silently replace what they deliberately put there.
        self.write({"workerId": "worker-001", "workerTokenProtected": "not-ours"})

        loaded = self.load()

        self.assertIs(loaded.state, CredentialState.UNUSABLE)
        self.assertIn("CREDENTIAL_DECRYPTION_FAILED", loaded.detail)

    def test_the_failure_never_carries_the_ciphertext(self):
        self.write({"workerId": "w", "workerTokenProtected": "ciphertext-value"})

        self.assertNotIn("ciphertext-value", self.load().detail)

    def test_an_id_without_a_token_is_unusable(self):
        self.write({"workerId": "worker-001"})

        self.assertIs(self.load().state, CredentialState.UNUSABLE)

    def test_a_token_without_an_id_is_unusable(self):
        self.write({"workerTokenProtected": seal("t")})

        self.assertIs(self.load().state, CredentialState.UNUSABLE)

    def test_a_file_that_is_not_json_is_unusable(self):
        self.path.write_text("{ half written", encoding="utf-8")

        self.assertIs(self.load().state, CredentialState.UNUSABLE)

    def test_a_json_array_is_not_a_settings_file(self):
        self.path.write_text("[]", encoding="utf-8")

        self.assertIs(self.load().state, CredentialState.UNUSABLE)

    def test_a_token_that_decrypts_to_nothing_is_unusable(self):
        self.write({"workerId": "w", "workerTokenProtected": seal("")})

        self.assertIs(self.load().state, CredentialState.UNUSABLE)


@unittest.skipUnless(os.name == "nt", "DPAPI is Windows-only")
class ThroughRealWindowsDpapiTest(CredentialTestCase):
    """No fake protector below this line."""

    def test_a_token_sealed_by_windows_comes_back(self):
        store_credentials("worker-001", "a-real-secret", self.directory)

        loaded = load_credentials(self.directory)

        self.assertIs(loaded.state, CredentialState.PRESENT)
        self.assertEqual(loaded.credentials.worker_token, "a-real-secret")

    def test_the_sealed_value_does_not_contain_the_token(self):
        store_credentials("worker-001", "a-real-secret", self.directory)

        self.assertNotIn("a-real-secret", self.path.read_text(encoding="utf-8"))

    def test_a_tampered_value_is_unusable_rather_than_a_crash(self):
        store_credentials("worker-001", "a-real-secret", self.directory)
        values = json.loads(self.path.read_text(encoding="utf-8"))
        sealed = values["workerTokenProtected"]
        # Flip a character in the middle of the ciphertext.
        middle = len(sealed) // 2
        swapped = "A" if sealed[middle] != "A" else "B"
        values["workerTokenProtected"] = sealed[:middle] + swapped + sealed[middle + 1:]
        self.write(values)

        self.assertIs(load_credentials(self.directory).state, CredentialState.UNUSABLE)

    def test_a_value_that_is_not_base64_is_unusable(self):
        self.write({"workerId": "w", "workerTokenProtected": "!!! not base64 !!!"})

        self.assertIs(load_credentials(self.directory).state, CredentialState.UNUSABLE)


if __name__ == "__main__":
    unittest.main()
