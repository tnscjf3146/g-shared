"""The Worker API over HTTP, driven with recorded answers.

Three endpoints, and the thing that makes them different from signup and login:
the state is read from the **HTTP status**, not from a word in the body. So the
statuses are what these tests are about.

Nothing here proves the Backend implements any of it -- it does not yet. What
is proved is that the Worker sends the agreed request and reads the agreed
answer.
"""

from __future__ import annotations

import io
import json
import socket
import unittest
import urllib.error

from gshared_worker.backend_client import BackendClient
from gshared_worker.worker_auth import WorkerAuthState
from gshared_worker.worker_credentials import WorkerCredentials
from gshared_worker.worker_registry import (
    AUTHENTICATE_PATH,
    REGISTER_PATH,
    GpuUpdate,
    HttpWorkerRegistry,
    WorkerRegistrationRequest,
    gpu_path,
)

BASE = "http://localhost:8080"

STORED = WorkerCredentials("worker-001", "the-worker-token")

OFFERED = WorkerRegistrationRequest(
    device_name="DESKTOP-MR7IIH9",
    os_version="Windows 10.0.26200",
    gpu_model="NVIDIA GeForce RTX 4070 Laptop GPU",
    vram_mb=8188,
    driver_version="596.36",
    benchmark_tokens_per_sec=41.5,
)


class Response(io.BytesIO):
    def __init__(self, payload=b"", status=200):
        super().__init__(payload)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
        return False


def answering(status=200, body=None, *, raises=None):
    """An opener that records the request and answers as told."""
    sent = {}

    def opener(request, timeout=None):
        sent["url"] = request.full_url
        sent["method"] = request.get_method()
        sent["headers"] = dict(request.headers)
        sent["body"] = json.loads(request.data.decode("utf-8")) if request.data else None
        if raises is not None:
            raise raises
        payload = b"" if body is None else json.dumps(body).encode("utf-8")
        if status >= 400:
            raise urllib.error.HTTPError(
                request.full_url, status, "refused", {}, io.BytesIO(payload))
        return Response(payload, status)

    return opener, sent


def registry(status=200, body=None, *, raises=None):
    opener, sent = answering(status, body, raises=raises)
    return HttpWorkerRegistry(BackendClient(BASE, opener=opener)), sent


class RegisteringThisMachineTest(unittest.TestCase):
    def test_it_posts_to_the_agreed_path(self):
        api, sent = registry(200, {"workerId": "w-1", "workerToken": "t-1"})

        api.register("user-jwt", OFFERED)

        self.assertEqual(sent["url"], BASE + REGISTER_PATH)
        self.assertEqual(sent["method"], "POST")

    def test_the_body_is_exactly_the_agreed_six_fields(self):
        api, sent = registry(200, {"workerId": "w-1", "workerToken": "t-1"})

        api.register("user-jwt", OFFERED)

        self.assertEqual(sent["body"], {
            "deviceName": "DESKTOP-MR7IIH9",
            "osVersion": "Windows 10.0.26200",
            "gpuModel": "NVIDIA GeForce RTX 4070 Laptop GPU",
            "vramMb": 8188,
            "driverVersion": "596.36",
            "benchmarkTokensPerSec": 41.5,
        })

    def test_it_is_authorised_with_the_users_access_token(self):
        # Not the Worker's: this machine has no identity yet, and the account
        # it joins is decided by the Backend from this token.
        api, sent = registry(200, {"workerId": "w-1", "workerToken": "t-1"})

        api.register("user-jwt", OFFERED)

        self.assertEqual(sent["headers"].get("Authorization"), "Bearer user-jwt")

    def test_no_user_id_is_sent(self):
        api, sent = registry(200, {"workerId": "w-1", "workerToken": "t-1"})

        api.register("user-jwt", OFFERED)

        self.assertNotIn("userId", sent["body"])

    def test_the_issued_identity_is_read_from_the_answer(self):
        api, _ = registry(200, {"workerId": "w-42", "workerToken": "issued-token"})

        answered = api.register("user-jwt", OFFERED)

        self.assertIs(answered.state, WorkerAuthState.REGISTERED)
        self.assertEqual(answered.credentials.worker_id, "w-42")
        self.assertEqual(answered.credentials.worker_token, "issued-token")

    def test_an_answer_without_an_identity_yields_no_credentials(self):
        api, _ = registry(200, {"workerId": "w-42"})

        answered = api.register("user-jwt", OFFERED)

        self.assertIsNone(answered.credentials)

    def test_a_blank_token_is_not_an_identity(self):
        api, _ = registry(200, {"workerId": "w-42", "workerToken": "   "})

        self.assertIsNone(api.register("user-jwt", OFFERED).credentials)

    def test_a_refused_registration_carries_the_backends_message(self):
        api, _ = registry(401, {"code": "INVALID_TOKEN", "message": "토큰이 유효하지 않습니다."})

        answered = api.register("user-jwt", OFFERED)

        self.assertIs(answered.state, WorkerAuthState.AUTH_FAILED)
        self.assertIn("유효하지", answered.detail)
        self.assertIsNone(answered.credentials)


class AuthenticatingAnExistingWorkerTest(unittest.TestCase):
    def test_it_posts_the_worker_id_to_the_agreed_path(self):
        api, sent = registry(200, {"workerId": "worker-001"})

        api.verify(STORED)

        self.assertEqual(sent["url"], BASE + AUTHENTICATE_PATH)
        self.assertEqual(sent["method"], "POST")
        self.assertEqual(sent["body"], {"workerId": "worker-001"})

    def test_it_is_authorised_with_the_worker_token(self):
        api, sent = registry(200, {"workerId": "worker-001"})

        api.verify(STORED)

        self.assertEqual(
            sent["headers"].get("Authorization"), "Bearer the-worker-token")

    def test_200_is_registered(self):
        # No REGISTERED string is expected in the body. The status is the answer.
        api, _ = registry(200, {"workerId": "worker-001"})

        self.assertIs(api.verify(STORED).state, WorkerAuthState.REGISTERED)

    def test_200_without_a_body_is_still_registered(self):
        api, _ = registry(200)

        self.assertIs(api.verify(STORED).state, WorkerAuthState.REGISTERED)

    def test_a_200_naming_a_different_worker_is_refused(self):
        # Carrying on would run work under the wrong identity.
        api, _ = registry(200, {"workerId": "somebody-elses-worker"})

        self.assertIs(api.verify(STORED).state, WorkerAuthState.AUTH_FAILED)

    def test_401_is_auth_failed(self):
        api, _ = registry(401, {"code": "INVALID_TOKEN", "message": "refused"})

        self.assertIs(api.verify(STORED).state, WorkerAuthState.AUTH_FAILED)

    def test_403_is_revoked(self):
        api, _ = registry(403, {"code": "WORKER_REVOKED", "message": "removed"})

        self.assertIs(api.verify(STORED).state, WorkerAuthState.REVOKED)

    def test_404_saying_worker_not_found_is_its_own_state(self):
        # The service looked and found nothing, and said so in as many words.
        api, _ = registry(404, {"code": "WORKER_NOT_FOUND", "message": "no such Worker"})

        answered = api.verify(STORED)

        self.assertIs(answered.state, WorkerAuthState.WORKER_NOT_FOUND)
        self.assertIn("no record", answered.detail)

    def test_a_bare_404_is_a_contract_error_not_a_revocation(self):
        # This build's own situation: the endpoint is not written yet. Reading
        # it as a revocation would tell a supplier their machine was removed
        # from their account because a path is missing.
        api, _ = registry(404, {"code": "NOT_FOUND", "message": "No static resource"})

        answered = api.verify(STORED)

        self.assertIs(answered.state, WorkerAuthState.BACKEND_CONTRACT_ERROR)
        self.assertIsNot(answered.state, WorkerAuthState.REVOKED)

    def test_a_404_with_no_body_at_all_is_a_contract_error(self):
        api, _ = registry(404)

        self.assertIs(
            api.verify(STORED).state, WorkerAuthState.BACKEND_CONTRACT_ERROR)

    def test_no_404_ever_yields_revoked(self):
        for body in (None, {"code": "WORKER_NOT_FOUND", "message": "gone"},
                     {"code": "NOT_FOUND", "message": "?"},
                     {"message": "no code at all"}):
            api, _ = registry(404, body)

            self.assertIsNot(api.verify(STORED).state, WorkerAuthState.REVOKED)

    def test_no_404_ever_asks_for_enrolment(self):
        # Nothing about a 404 may start a new registration.
        for body in (None, {"code": "WORKER_NOT_FOUND", "message": "gone"}):
            api, _ = registry(404, body)

            self.assertIsNot(
                api.verify(STORED).state, WorkerAuthState.NEEDS_ENROLLMENT)

    def test_only_403_is_a_revocation(self):
        api, _ = registry(403, {"code": "WORKER_REVOKED", "message": "removed"})

        self.assertIs(api.verify(STORED).state, WorkerAuthState.REVOKED)

    def test_a_missing_register_endpoint_is_a_contract_error_too(self):
        api, _ = registry(404, {"code": "NOT_FOUND", "message": "no handler"})

        answered = api.register("user-jwt", OFFERED)

        self.assertIs(answered.state, WorkerAuthState.BACKEND_CONTRACT_ERROR)
        self.assertIsNone(answered.credentials)

    def test_a_server_fault_is_not_an_auth_failure(self):
        # A 500 says nothing about this Worker. Treating it as a refusal would
        # tell somebody with a good machine to register it again.
        api, _ = registry(500, {"code": "INTERNAL", "message": "boom"})

        self.assertIs(api.verify(STORED).state, WorkerAuthState.BACKEND_UNREACHABLE)


class WhenNothingAnswersTest(unittest.TestCase):
    def test_a_refused_connection_is_unreachable(self):
        api, _ = registry(raises=urllib.error.URLError("connection refused"))

        self.assertIs(api.verify(STORED).state, WorkerAuthState.BACKEND_UNREACHABLE)

    def test_a_timeout_is_unreachable(self):
        api, _ = registry(raises=socket.timeout("timed out"))

        self.assertIs(api.verify(STORED).state, WorkerAuthState.BACKEND_UNREACHABLE)

    def test_it_is_never_read_as_needing_enrolment_or_revocation(self):
        api, _ = registry(raises=urllib.error.URLError("down"))

        state = api.verify(STORED).state

        self.assertIsNot(state, WorkerAuthState.NEEDS_ENROLLMENT)
        self.assertIsNot(state, WorkerAuthState.REVOKED)

    def test_an_unreachable_service_registers_nothing(self):
        api, _ = registry(raises=urllib.error.URLError("down"))

        answered = api.register("user-jwt", OFFERED)

        self.assertIs(answered.state, WorkerAuthState.BACKEND_UNREACHABLE)
        self.assertIsNone(answered.credentials)


class UpdatingTheGpuTest(unittest.TestCase):
    UPDATE = GpuUpdate(
        gpu_model="NVIDIA GeForce RTX 4070 Laptop GPU",
        vram_mb=8188,
        driver_version="596.36",
        benchmark_tokens_per_sec=52.25,
    )

    def test_it_puts_to_the_workers_own_path(self):
        api, sent = registry(204)

        api.update_gpu(STORED, self.UPDATE)

        self.assertEqual(sent["url"], BASE + "/api/workers/worker-001/gpu")
        self.assertEqual(sent["method"], "PUT")

    def test_the_body_is_the_agreed_four_fields(self):
        api, sent = registry(204)

        api.update_gpu(STORED, self.UPDATE)

        self.assertEqual(sent["body"], {
            "gpuModel": "NVIDIA GeForce RTX 4070 Laptop GPU",
            "vramMb": 8188,
            "driverVersion": "596.36",
            "benchmarkTokensPerSec": 52.25,
        })

    def test_it_is_authorised_with_the_worker_token(self):
        api, sent = registry(204)

        api.update_gpu(STORED, self.UPDATE)

        self.assertEqual(
            sent["headers"].get("Authorization"), "Bearer the-worker-token")

    def test_204_with_no_body_is_accepted(self):
        api, _ = registry(204)

        self.assertIs(
            api.update_gpu(STORED, self.UPDATE).state, WorkerAuthState.REGISTERED)

    def test_a_revoked_worker_cannot_update_its_gpu(self):
        api, _ = registry(403, {"code": "WORKER_REVOKED", "message": "removed"})

        self.assertIs(
            api.update_gpu(STORED, self.UPDATE).state, WorkerAuthState.REVOKED)

    def test_a_worker_id_is_escaped_into_the_path(self):
        self.assertEqual(gpu_path("a/b c"), "/api/workers/a%2Fb%20c/gpu")


class TheFieldNameTest(unittest.TestCase):
    """``benchmarkScore`` is not part of this API. ``benchmarkTokensPerSec`` is."""

    def test_registration_uses_benchmark_tokens_per_sec(self):
        self.assertIn("benchmarkTokensPerSec", OFFERED.as_payload())

    def test_registration_never_uses_benchmark_score(self):
        self.assertNotIn("benchmarkScore", json.dumps(OFFERED.as_payload()))

    def test_the_gpu_update_never_uses_benchmark_score(self):
        payload = OFFERED.gpu_update().as_payload()

        self.assertIn("benchmarkTokensPerSec", payload)
        self.assertNotIn("benchmarkScore", json.dumps(payload))

    def test_the_gpu_update_carries_the_same_facts_as_the_registration(self):
        update = OFFERED.gpu_update()

        self.assertEqual(update.gpu_model, OFFERED.gpu_model)
        self.assertEqual(update.vram_mb, OFFERED.vram_mb)
        self.assertEqual(update.driver_version, OFFERED.driver_version)
        self.assertEqual(
            update.benchmark_tokens_per_sec, OFFERED.benchmark_tokens_per_sec)


class NoTokenIsEverLoggedTest(unittest.TestCase):
    """The header is the credential, so it is the thing that must not be logged."""

    def setUp(self):
        import logging
        from gshared_worker.logging_setup import LOGGER_NAME

        self.records = []
        handler = logging.Handler()
        handler.emit = self.records.append
        logger = logging.getLogger(LOGGER_NAME)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        self.addCleanup(logger.removeHandler, handler)

    @property
    def logged(self) -> str:
        return "\n".join(record.getMessage() for record in self.records)

    def test_the_worker_token_is_not_logged(self):
        api, _ = registry(200, {"workerId": "worker-001"})

        api.verify(STORED)

        self.assertNotIn("the-worker-token", self.logged)

    def test_the_access_token_is_not_logged(self):
        api, _ = registry(200, {"workerId": "w-1", "workerToken": "t-1"})

        api.register("a-real-user-jwt", OFFERED)

        self.assertNotIn("a-real-user-jwt", self.logged)

    def test_the_issued_worker_token_is_not_logged(self):
        api, _ = registry(200, {"workerId": "w-1", "workerToken": "freshly-issued"})

        api.register("user-jwt", OFFERED)

        self.assertNotIn("freshly-issued", self.logged)

    def test_the_path_is_logged_so_a_failure_can_be_traced(self):
        api, _ = registry(200, {"workerId": "worker-001"})

        api.verify(STORED)

        self.assertIn(AUTHENTICATE_PATH, self.logged)


if __name__ == "__main__":
    unittest.main()
