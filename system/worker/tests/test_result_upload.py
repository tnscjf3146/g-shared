"""Getting the artifact into the Backend's storage, and knowing that it is.

Written against ``WorkerJobController``, ``WorkerJobService`` and
``LocalObjectStorage``, not against the circulating spec:

* ``POST /api/worker/jobs/{jobId}/result-upload`` takes ``fileName`` and
  ``contentType``; the Backend builds ``jobs/{jobId}/`` + fileName itself and
  answers ``objectKey``, ``uploadUrl``, ``expiresAt``.
* ``PUT`` the bytes at that URL. The signature covers method, key and expiry
  and nothing else -- ``presignUpload`` discards the content type and
  ``StorageController`` reads the body straight from the stream.
* ``POST .../result-upload/complete`` takes ``objectKey``, ``sizeBytes`` and
  ``sha256`` **without 0x**, and the Backend re-hashes the stored object
  before it believes any of them.

There is no "verified hash" in the response to compare against. A mismatch is
an error status, so a 200 *is* the verification -- which is why the tests
below check what happens on a non-200 rather than what a field said.
"""

from __future__ import annotations

import io
import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp

from gshared_worker.result_upload import (
    UploadRefused,
    check_upload_url,
    complete_path,
    complete_upload,
    put_artifact,
    request_ticket,
    upload_path,
    upload_result,
)

TOKEN = "worker-token-never-logged"
JOB = "123"
SHA = "ab" * 32
SIGNED = "https://backend.example/storage/jobs/123/result.tar?method=PUT&expires=1&sig=SECRETSIG"


@dataclass
class Answer:
    """A BackendAnswer, as far as this module looks at one."""

    succeeded: bool = True
    body: object = None
    code: str = ""
    status: int | None = 200


class FakeBackend:
    """Answers the two endpoints the way the Java does."""

    def __init__(self, *, ticket=None, complete=None, fail_ticket=None,
                 fail_complete=None):
        self.calls: list[tuple] = []
        self._ticket = ticket if ticket is not None else {
            "objectKey": f"jobs/{JOB}/result.tar",
            "uploadUrl": SIGNED,
            "expiresAt": "2026-09-11T06:00:00Z",
        }
        self._complete = complete if complete is not None else {
            "jobId": JOB, "objectKey": f"jobs/{JOB}/result.tar",
            "verified": True,
        }
        self._fail_ticket = fail_ticket
        self._fail_complete = fail_complete

    def request(self, method, path, payload=None, *, bearer=None):
        self.calls.append((method, path, payload, bearer))
        if path.endswith("/complete"):
            if self._fail_complete is not None:
                return self._fail_complete
            return Answer(body=self._complete)
        if self._fail_ticket is not None:
            return self._fail_ticket
        return Answer(body=self._ticket)

    def of(self, suffix: str) -> list[tuple]:
        return [c for c in self.calls if c[1].endswith(suffix)]


class Storage:
    """Stands in for the signed-URL endpoint. Records what it was sent."""

    def __init__(self, *, status=200, raise_with=None, fail_times=0):
        self.received: list[bytes] = []
        self.headers: list[dict] = []
        self.urls: list[str] = []
        self._status = status
        self._raise = raise_with
        self._fail_times = fail_times

    def __call__(self, request, timeout=None):
        self.urls.append(request.full_url)
        self.headers.append(dict(request.headers))
        if self._fail_times > 0:
            self._fail_times -= 1
            raise OSError("the connection dropped")
        if self._raise is not None:
            raise self._raise
        self.received.append(request.data.read())
        return _Response(self._status)


class _Response:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def an_artifact(content: bytes = b"tar bytes" * 100) -> Path:
    path = Path(mkdtemp()) / "result.tar"
    path.write_bytes(content)
    return path


class TestThePaths(unittest.TestCase):
    def test_they_are_the_ones_the_controller_maps(self):
        self.assertEqual(upload_path("7"), "/api/worker/jobs/7/result-upload")
        self.assertEqual(complete_path("7"),
                         "/api/worker/jobs/7/result-upload/complete")


class TestAskingWhereToPutIt(unittest.TestCase):
    def test_the_request_carries_what_the_dto_requires(self):
        backend = FakeBackend()

        request_ticket(backend, TOKEN, JOB)

        method, path, payload, bearer = backend.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(path, upload_path(JOB))
        self.assertEqual(payload["fileName"], "result.tar")
        self.assertEqual(payload["contentType"], "application/x-tar")
        self.assertEqual(bearer, TOKEN)

    def test_the_file_name_fits_the_backends_pattern(self):
        import re

        backend = FakeBackend()
        request_ticket(backend, TOKEN, JOB)

        name = backend.calls[0][2]["fileName"]
        self.assertRegex(name, re.compile(r"^[A-Za-z0-9._-]{1,128}$"))

    def test_the_object_key_comes_back_from_the_backend(self):
        # Never built here. WorkerJobService makes it jobs/{id}/ + fileName
        # and checks that prefix again on completion.
        ticket = request_ticket(FakeBackend(), TOKEN, JOB)

        self.assertEqual(ticket.object_key, "jobs/123/result.tar")

    def test_a_ticket_with_no_url_is_refused(self):
        backend = FakeBackend(ticket={"objectKey": "jobs/123/result.tar"})

        with self.assertRaises(UploadRefused):
            request_ticket(backend, TOKEN, JOB)

    def test_a_ticket_with_no_object_key_is_refused(self):
        backend = FakeBackend(ticket={"uploadUrl": SIGNED})

        with self.assertRaises(UploadRefused):
            request_ticket(backend, TOKEN, JOB)

    def test_a_refusal_is_passed_on(self):
        backend = FakeBackend(
            fail_ticket=Answer(succeeded=False, code="INVALID_JOB_STATUS",
                               status=400))

        with self.assertRaises(UploadRefused) as raised:
            request_ticket(backend, TOKEN, JOB)

        self.assertFalse(raised.exception.transient)


class TestTheUrlIsCheckedLikeAnyOther(unittest.TestCase):
    def test_http_and_https_are_allowed(self):
        for url in ("http://backend.example/storage/x?sig=1",
                    "https://backend.example/storage/x?sig=1"):
            with self.subTest(url=url):
                self.assertEqual(check_upload_url(url), url)

    def test_anything_else_is_refused(self):
        for url in ("file:///C:/Users/SSAFY/.gshared/credentials.json",
                    "ftp://elsewhere/x", "gopher://x", "", "jobs/1/result.tar",
                    "https://"):
            with self.subTest(url=url):
                with self.assertRaises(UploadRefused):
                    check_upload_url(url)


class TestSendingTheBytes(unittest.TestCase):
    def test_the_body_is_the_file(self):
        artifact = an_artifact(b"exactly these bytes")
        storage = Storage()

        put_artifact(SIGNED, artifact, artifact.stat().st_size,
                     opener=storage)

        self.assertEqual(storage.received, [b"exactly these bytes"])

    def test_it_is_a_put(self):
        artifact = an_artifact()
        storage = Storage()

        put_artifact(SIGNED, artifact, artifact.stat().st_size, opener=storage)

        self.assertEqual(len(storage.urls), 1)

    def test_the_file_is_streamed_not_read_into_memory(self):
        """urllib is handed the open file, not its contents.

        An adapter is tens to hundreds of megabytes and this process is also
        holding a WebSocket open.
        """
        artifact = an_artifact()
        handed: list = []

        def opener(request, timeout=None):
            handed.append(request.data)
            request.data.read()
            return _Response(200)

        put_artifact(SIGNED, artifact, artifact.stat().st_size, opener=opener)

        self.assertTrue(hasattr(handed[0], "read"))
        self.assertNotIsInstance(handed[0], (bytes, bytearray))

    def test_the_length_is_declared(self):
        artifact = an_artifact(b"x" * 4096)
        storage = Storage()

        put_artifact(SIGNED, artifact, 4096, opener=storage)

        headers = {k.lower(): v for k, v in storage.headers[0].items()}
        self.assertEqual(headers["Content-length".lower()], "4096")

    def test_the_content_type_is_the_one_the_ticket_asked_for(self):
        artifact = an_artifact()
        storage = Storage()

        put_artifact(SIGNED, artifact, artifact.stat().st_size, opener=storage)

        headers = {k.lower(): v for k, v in storage.headers[0].items()}
        self.assertEqual(headers["content-type"], "application/x-tar")

    def test_no_credential_header_is_added(self):
        # The signature in the URL is the authentication. StorageController
        # reads no Authorization header and a token here would be one more
        # place the token exists.
        artifact = an_artifact()
        storage = Storage()

        put_artifact(SIGNED, artifact, artifact.stat().st_size, opener=storage)

        for header in storage.headers[0]:
            self.assertNotIn("authorization", header.lower())

    def test_a_server_error_is_worth_another_go(self):
        import urllib.error

        artifact = an_artifact()
        storage = Storage(raise_with=urllib.error.HTTPError(
            SIGNED, 503, "busy", {}, io.BytesIO(b"")))

        with self.assertRaises(UploadRefused) as raised:
            put_artifact(SIGNED, artifact, 1, opener=storage)

        self.assertTrue(raised.exception.transient)

    def test_a_refusal_is_not(self):
        import urllib.error

        artifact = an_artifact()
        storage = Storage(raise_with=urllib.error.HTTPError(
            SIGNED, 400, "bad", {}, io.BytesIO(b"")))

        with self.assertRaises(UploadRefused) as raised:
            put_artifact(SIGNED, artifact, 1, opener=storage)

        self.assertFalse(raised.exception.transient)

    def test_the_signed_url_is_never_in_the_message(self):
        import urllib.error

        artifact = an_artifact()
        storage = Storage(raise_with=urllib.error.HTTPError(
            SIGNED, 403, "no", {}, io.BytesIO(b"")))

        with self.assertRaises(UploadRefused) as raised:
            put_artifact(SIGNED, artifact, 1, opener=storage)

        self.assertNotIn("SECRETSIG", str(raised.exception))
        self.assertNotIn("sig=", str(raised.exception))


class TestTellingTheBackendItIsThere(unittest.TestCase):
    def test_the_request_carries_what_the_dto_requires(self):
        backend = FakeBackend()

        complete_upload(backend, TOKEN, JOB,
                        object_key="jobs/123/result.tar",
                        sha256=SHA, size_bytes=4096)

        method, path, payload, bearer = backend.of("/complete")[0]
        self.assertEqual(method, "POST")
        self.assertEqual(path, complete_path(JOB))
        self.assertEqual(payload["objectKey"], "jobs/123/result.tar")
        self.assertEqual(payload["sizeBytes"], 4096)
        self.assertEqual(payload["sha256"], SHA)
        self.assertEqual(bearer, TOKEN)

    def test_the_hash_goes_without_the_prefix(self):
        # ResultUploadCompleteRequest validates ^[0-9a-fA-F]{64}$, "0x 없이".
        backend = FakeBackend()

        complete_upload(backend, TOKEN, JOB, object_key="jobs/123/result.tar",
                        sha256=SHA, size_bytes=1)

        sent = backend.of("/complete")[0][2]["sha256"]
        self.assertFalse(sent.startswith("0x"))
        self.assertEqual(len(sent), 64)

    def test_the_size_is_a_whole_number(self):
        # @Positive Long. A float would fail validation.
        backend = FakeBackend()

        complete_upload(backend, TOKEN, JOB, object_key="jobs/123/result.tar",
                        sha256=SHA, size_bytes=4096)

        self.assertIsInstance(backend.of("/complete")[0][2]["sizeBytes"], int)

    def test_a_mismatch_is_an_error_status_and_is_raised(self):
        """How the Backend reports a hash that did not match.

        ``completeResultUpload`` stats the object, hashes it itself and throws
        RESULT_UPLOAD_MISMATCH. There is no verified-hash field to compare --
        the 200 is the comparison.
        """
        backend = FakeBackend(fail_complete=Answer(
            succeeded=False, code="RESULT_UPLOAD_MISMATCH", status=400))

        with self.assertRaises(UploadRefused) as raised:
            complete_upload(backend, TOKEN, JOB,
                            object_key="jobs/123/result.tar",
                            sha256=SHA, size_bytes=1)

        self.assertFalse(raised.exception.transient, "a mismatch is settled")

    def test_a_different_object_coming_back_is_refused(self):
        backend = FakeBackend(complete={"jobId": JOB,
                                        "objectKey": "jobs/999/result.tar",
                                        "verified": True})

        with self.assertRaises(UploadRefused):
            complete_upload(backend, TOKEN, JOB,
                            object_key="jobs/123/result.tar",
                            sha256=SHA, size_bytes=1)

    def test_an_unverified_answer_is_refused(self):
        backend = FakeBackend(complete={"objectKey": "jobs/123/result.tar",
                                        "verified": False})

        with self.assertRaises(UploadRefused):
            complete_upload(backend, TOKEN, JOB,
                            object_key="jobs/123/result.tar",
                            sha256=SHA, size_bytes=1)


class TestTheWholeUpload(unittest.TestCase):
    def test_the_three_steps_happen_in_order(self):
        backend = FakeBackend()
        storage = Storage()
        artifact = an_artifact()

        stored = upload_result(backend, TOKEN, JOB, artifact,
                               sha256=SHA, size_bytes=artifact.stat().st_size,
                               opener=storage)

        self.assertEqual([c[1] for c in backend.calls],
                         [upload_path(JOB), complete_path(JOB)])
        self.assertEqual(len(storage.received), 1)
        self.assertEqual(stored.object_key, "jobs/123/result.tar")

    def test_complete_is_not_called_when_the_put_failed(self):
        """Nothing downstream may believe the object is there.

        submitResult writes a hash that cannot be corrected, and it is the
        completion that proves the Backend has the bytes behind it.
        """
        backend = FakeBackend()
        storage = Storage(raise_with=ValueError("kaboom"))
        artifact = an_artifact()

        with self.assertRaises(Exception):
            upload_result(backend, TOKEN, JOB, artifact, sha256=SHA,
                          size_bytes=1, opener=storage, attempts=1)

        self.assertEqual(backend.of("/complete"), [])

    def test_a_dropped_connection_is_tried_again(self):
        backend = FakeBackend()
        storage = Storage(fail_times=1)
        artifact = an_artifact()

        upload_result(backend, TOKEN, JOB, artifact, sha256=SHA,
                      size_bytes=artifact.stat().st_size, opener=storage,
                      sleep=lambda seconds: True)

        self.assertEqual(len(storage.urls), 2)
        self.assertEqual(len(storage.received), 1)

    def test_a_retry_asks_for_a_fresh_ticket(self):
        # The old URL may simply have expired, and createResultUpload has no
        # once-only rule -- it re-signs the same key.
        backend = FakeBackend()
        storage = Storage(fail_times=1)
        artifact = an_artifact()

        upload_result(backend, TOKEN, JOB, artifact, sha256=SHA,
                      size_bytes=artifact.stat().st_size, opener=storage,
                      sleep=lambda seconds: True)

        self.assertEqual(len(backend.of("/result-upload")), 2,
                         "the second attempt reused the old URL")

    def test_retrying_is_bounded(self):
        backend = FakeBackend()
        storage = Storage(fail_times=99)
        artifact = an_artifact()

        with self.assertRaises(UploadRefused):
            upload_result(backend, TOKEN, JOB, artifact, sha256=SHA,
                          size_bytes=1, opener=storage,
                          sleep=lambda seconds: True, attempts=3)

        self.assertLessEqual(len(storage.urls), 3)

    def test_a_mismatch_is_never_retried(self):
        """Asking again cannot change which of the two numbers is wrong.

        A Worker that kept trying until they agreed would be a Worker that
        eventually uploaded something it had not checked.
        """
        backend = FakeBackend(fail_complete=Answer(
            succeeded=False, code="RESULT_UPLOAD_MISMATCH", status=400))
        storage = Storage()
        artifact = an_artifact()

        with self.assertRaises(UploadRefused):
            upload_result(backend, TOKEN, JOB, artifact, sha256=SHA,
                          size_bytes=artifact.stat().st_size, opener=storage,
                          sleep=lambda seconds: True)

        self.assertEqual(len(backend.of("/complete")), 1)

    def test_a_worker_that_is_stopping_does_not_wait_out_the_budget(self):
        backend = FakeBackend()
        storage = Storage(fail_times=99)
        artifact = an_artifact()

        with self.assertRaises(UploadRefused):
            upload_result(backend, TOKEN, JOB, artifact, sha256=SHA,
                          size_bytes=1, opener=storage,
                          sleep=lambda seconds: False)

        self.assertEqual(len(storage.urls), 1)


class TestNothingSecretIsLogged(unittest.TestCase):
    def test_the_module_never_logs_the_url(self):
        import ast
        import inspect

        from gshared_worker import result_upload

        tree = ast.parse(inspect.getsource(result_upload))
        logged: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "_debug"):
                logged.append(ast.unparse(node))

        self.assertTrue(logged, "the module logs nothing at all?")
        for line in logged:
            for forbidden in ("upload_url", "url", "ticket.", "sig",
                              "worker_token", "bearer", "_token"):
                with self.subTest(line=line, forbidden=forbidden):
                    self.assertNotIn(forbidden, line)

    def test_a_refusal_carries_no_url(self):
        backend = FakeBackend(ticket={"objectKey": "k",
                                      "uploadUrl": "file:///C:/secrets.json"})

        with self.assertRaises(UploadRefused) as raised:
            request_ticket(backend, TOKEN, JOB)

        self.assertNotIn("secrets.json", str(raised.exception))
        self.assertNotIn("file://", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
