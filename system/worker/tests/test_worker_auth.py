"""Whose machine this is, and the states that must never be confused.

The one that matters most: **BACKEND_UNREACHABLE is not NEEDS_ENROLLMENT**. A
service that is down for a minute must never make a registered machine throw
its identity away and ask somebody to sign in again.
"""

from __future__ import annotations

import unittest

from gshared_worker.worker_auth import (
    RegistryAnswer,
    WorkerAuthService,
    WorkerAuthState,
)
from gshared_worker.worker_registry import WorkerRegistrationRequest
from gshared_worker.worker_credentials import (
    CredentialLoad,
    CredentialState,
    WorkerCredentials,
)

STORED = WorkerCredentials("worker-001", "the-token")

#: What the machine is offering. This module decides states, not contracts, so
#: it passes the request through untouched -- which is what is asserted below.
OFFERED = WorkerRegistrationRequest(
    operator_address="0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
    client_version="0.1.0",
    os_version="Windows 11",
    wsl_available=True,
    gpu_model="NVIDIA GeForce RTX 4070 Laptop GPU",
    vram_mb=8188,
    benchmark_tokens_per_sec=29048.535,
    benchmark_version="gshared-benchmark-v1",
)


def present() -> CredentialLoad:
    return CredentialLoad(CredentialState.PRESENT, STORED, "stored credentials")


def absent() -> CredentialLoad:
    return CredentialLoad(CredentialState.ABSENT, detail="no file")


def unusable() -> CredentialLoad:
    return CredentialLoad(
        CredentialState.UNUSABLE,
        detail="CREDENTIAL_DECRYPTION_FAILED: not readable on this account")


class Registry:
    """The Backend's Worker side, answering as a test says."""

    def __init__(self, verifies=None, registers=None):
        self._verifies = verifies or RegistryAnswer(
            WorkerAuthState.REGISTERED, "accepted")
        self._registers = registers
        self.verified: list[WorkerCredentials] = []
        self.registered: list[str] = []
        self.described: list = []

    def verify(self, credentials):
        self.verified.append(credentials)
        return self._verifies

    def register(self, access_token, registration):
        self.registered.append(access_token)
        self.described.append(registration)
        if self._registers is None:
            return RegistryAnswer(
                WorkerAuthState.REGISTERED, "registered",
                WorkerCredentials("worker-new", "issued-token"))
        return self._registers


class AnExistingWorkerTest(unittest.TestCase):
    def test_a_worker_the_backend_still_accepts_is_registered(self):
        registry = Registry()

        result = WorkerAuthService(registry).authenticate(present())

        self.assertIs(result.state, WorkerAuthState.REGISTERED)
        self.assertTrue(result.is_authenticated)
        self.assertEqual(result.worker_id, "worker-001")

    def test_the_stored_identity_is_what_gets_checked(self):
        registry = Registry()

        WorkerAuthService(registry).authenticate(present())

        self.assertEqual(registry.verified, [STORED])

    def test_a_revoked_worker_is_not_authenticated(self):
        registry = Registry(verifies=RegistryAnswer(
            WorkerAuthState.REVOKED, "this Worker was removed from the account"))

        result = WorkerAuthService(registry).authenticate(present())

        self.assertIs(result.state, WorkerAuthState.REVOKED)
        self.assertIsNone(result.worker_id)

    def test_a_refused_token_is_not_authenticated(self):
        registry = Registry(verifies=RegistryAnswer(
            WorkerAuthState.AUTH_FAILED, "the token was refused"))

        result = WorkerAuthService(registry).authenticate(present())

        self.assertIs(result.state, WorkerAuthState.AUTH_FAILED)


class ANewMachineTest(unittest.TestCase):
    def test_no_credentials_means_somebody_has_to_sign_in(self):
        result = WorkerAuthService(Registry()).authenticate(absent())

        self.assertIs(result.state, WorkerAuthState.NEEDS_ENROLLMENT)

    def test_nothing_is_asked_of_the_backend_before_that(self):
        registry = Registry()

        WorkerAuthService(registry).authenticate(absent())

        self.assertEqual(registry.verified, [])

    def test_registering_hands_the_access_token_over_once(self):
        registry = Registry()

        result, credentials = WorkerAuthService(registry).register("user-jwt", OFFERED)

        self.assertIs(result.state, WorkerAuthState.REGISTERED)
        self.assertEqual(registry.registered, ["user-jwt"])
        self.assertEqual(credentials.worker_id, "worker-new")
        self.assertEqual(credentials.worker_token, "issued-token")

    def test_what_the_machine_offers_is_passed_through_untouched(self):
        registry = Registry()

        WorkerAuthService(registry).register("user-jwt", OFFERED)

        self.assertEqual(registry.described, [OFFERED])

    def test_a_refused_registration_yields_nothing_to_store(self):
        # A half-registered machine is worse than an unregistered one.
        registry = Registry(registers=RegistryAnswer(
            WorkerAuthState.AUTH_FAILED, "that account cannot add Workers"))

        result, credentials = WorkerAuthService(registry).register("user-jwt", OFFERED)

        self.assertIsNone(credentials)
        self.assertFalse(result.is_authenticated)

    def test_a_registration_that_issued_no_identity_is_not_a_success(self):
        registry = Registry(registers=RegistryAnswer(
            WorkerAuthState.REGISTERED, "done", credentials=None))

        result, credentials = WorkerAuthService(registry).register("user-jwt", OFFERED)

        self.assertIsNone(credentials)
        self.assertIs(result.state, WorkerAuthState.AUTH_FAILED)

    def test_an_unreachable_backend_during_registration_stores_nothing(self):
        registry = Registry(registers=RegistryAnswer(
            WorkerAuthState.BACKEND_UNREACHABLE, "could not be reached"))

        result, credentials = WorkerAuthService(registry).register("user-jwt", OFFERED)

        self.assertIsNone(credentials)
        self.assertIs(result.state, WorkerAuthState.BACKEND_UNREACHABLE)


class CredentialsThatCannotBeUsedTest(unittest.TestCase):
    def test_they_are_a_state_of_their_own(self):
        result = WorkerAuthService(Registry()).authenticate(unusable())

        self.assertIs(result.state, WorkerAuthState.CREDENTIAL_ERROR)

    def test_the_machine_is_not_quietly_registered_again(self):
        # Somebody copied that file here deliberately. Replacing it is not
        # ours to decide.
        registry = Registry()

        result = WorkerAuthService(registry).authenticate(unusable())

        self.assertIsNot(result.state, WorkerAuthState.NEEDS_ENROLLMENT)
        self.assertEqual(registry.registered, [])

    def test_nothing_is_asked_of_the_backend(self):
        registry = Registry()

        WorkerAuthService(registry).authenticate(unusable())

        self.assertEqual(registry.verified, [])

    def test_why_it_cannot_be_used_is_carried_through(self):
        result = WorkerAuthService(Registry()).authenticate(unusable())

        self.assertIn("CREDENTIAL_DECRYPTION_FAILED", result.detail)


class AServiceThatIsDownIsNotAMachineThatIsUnregisteredTest(unittest.TestCase):
    """The distinction the whole state enum exists for."""

    def setUp(self):
        self.registry = Registry(verifies=RegistryAnswer(
            WorkerAuthState.BACKEND_UNREACHABLE,
            "the G-shared service could not be reached"))
        self.result = WorkerAuthService(self.registry).authenticate(present())

    def test_it_is_reported_as_unreachable(self):
        self.assertIs(self.result.state, WorkerAuthState.BACKEND_UNREACHABLE)

    def test_it_is_never_reported_as_needing_enrolment(self):
        self.assertIsNot(self.result.state, WorkerAuthState.NEEDS_ENROLLMENT)

    def test_it_is_never_reported_as_revoked(self):
        # Being unable to ask is not the same as being told no.
        self.assertIsNot(self.result.state, WorkerAuthState.REVOKED)

    def test_nothing_is_registered_again(self):
        self.assertEqual(self.registry.registered, [])

    def test_the_machine_is_simply_not_authenticated_this_run(self):
        self.assertFalse(self.result.is_authenticated)


class WhatIsNeverCarriedTest(unittest.TestCase):
    def test_a_result_never_carries_the_worker_token(self):
        result = WorkerAuthService(Registry()).authenticate(present())

        self.assertNotIn("the-token", result.detail)
        self.assertFalse(hasattr(result, "worker_token"))

    def test_a_result_never_carries_the_access_token(self):
        result, _ = WorkerAuthService(Registry()).register("user-jwt-value", OFFERED)

        self.assertNotIn("user-jwt-value", result.detail)
        self.assertFalse(hasattr(result, "access_token"))


if __name__ == "__main__":
    unittest.main()
