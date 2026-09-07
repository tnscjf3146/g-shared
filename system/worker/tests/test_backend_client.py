"""The Backend's real auth contract, driven with recorded answers.

Every shape here was taken from the Backend's own code, not from a
specification: signup answers 201 with an empty body, login is a Spring
Security filter that answers ``{"accessToken": ...}``, and every refusal is
``{"code", "message"}``.
"""

from __future__ import annotations

import io
import json
import socket
import unittest
import urllib.error

from gshared_worker.backend_client import (
    LOGIN_PATH,
    SIGNUP_PATH,
    BackendClient,
    Outcome,
)

BASE = "http://localhost:8080"


class Response(io.BytesIO):
    """Stands in for what urlopen returns, including the context manager."""

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
        sent["timeout"] = timeout
        if raises is not None:
            raise raises
        payload = b"" if body is None else json.dumps(body).encode("utf-8")
        if status >= 400:
            raise urllib.error.HTTPError(
                request.full_url, status, "refused", {}, io.BytesIO(payload))
        return Response(payload, status)

    return opener, sent


class SigningUpTest(unittest.TestCase):
    def test_it_posts_the_three_fields_the_backend_requires(self):
        opener, sent = answering(201)

        BackendClient(BASE, opener=opener).signup("a@b.com", "abcd1234", "sdk")

        self.assertEqual(sent["url"], BASE + SIGNUP_PATH)
        self.assertEqual(sent["method"], "POST")
        self.assertEqual(
            sent["body"], {"email": "a@b.com", "password": "abcd1234", "nickname": "sdk"})

    def test_201_with_an_empty_body_is_a_success(self):
        # The Backend issues no token here, so an empty body is the normal
        # answer rather than something that failed to parse.
        opener, _ = answering(201)

        answered = BackendClient(BASE, opener=opener).signup("a@b.com", "abcd1234", "sdk")

        self.assertTrue(answered.succeeded)

    def test_a_duplicate_email_carries_the_backends_own_code_and_message(self):
        opener, _ = answering(409, {
            "code": "DUPLICATE_EMAIL", "message": "이미 사용중인 이메일입니다."})

        answered = BackendClient(BASE, opener=opener).signup("a@b.com", "abcd1234", "sdk")

        self.assertIs(answered.outcome, Outcome.REFUSED)
        self.assertEqual(answered.code, "DUPLICATE_EMAIL")
        self.assertIn("이미 사용중인", answered.message)

    def test_a_validation_failure_carries_the_field_message(self):
        # The Backend replaces the generic message with the first field error.
        opener, _ = answering(400, {
            "code": "INVALID_INPUT", "message": "비밀번호는 6 ~ 20자입니다."})

        answered = BackendClient(BASE, opener=opener).signup("a@b.com", "x", "sdk")

        self.assertEqual(answered.code, "INVALID_INPUT")
        self.assertIn("6 ~ 20", answered.message)

    def test_rate_limiting_is_a_refusal_with_its_own_code(self):
        opener, _ = answering(429, {
            "code": "TOO_MANY_REQUESTS", "message": "요청이 너무 많습니다."})

        answered = BackendClient(BASE, opener=opener).signup("a@b.com", "abcd1234", "s")

        self.assertEqual(answered.code, "TOO_MANY_REQUESTS")


class LoggingInTest(unittest.TestCase):
    def test_it_posts_only_the_two_fields_the_filter_reads(self):
        opener, sent = answering(200, {"accessToken": "jwt-value"})

        BackendClient(BASE, opener=opener).login("a@b.com", "abcd1234")

        self.assertEqual(sent["url"], BASE + LOGIN_PATH)
        self.assertEqual(sent["body"], {"email": "a@b.com", "password": "abcd1234"})

    def test_the_access_token_is_read_from_the_body(self):
        opener, _ = answering(200, {"accessToken": "jwt-value"})

        answered = BackendClient(BASE, opener=opener).login("a@b.com", "abcd1234")

        self.assertTrue(answered.succeeded)
        self.assertEqual(answered.body["accessToken"], "jwt-value")

    def test_wrong_credentials_are_a_refusal_not_an_outage(self):
        opener, _ = answering(401, {
            "code": "INVALID_CREDENTIALS",
            "message": "아이디 또는 비밀번호가 올바르지 않습니다."})

        answered = BackendClient(BASE, opener=opener).login("a@b.com", "wrong")

        self.assertIs(answered.outcome, Outcome.REFUSED)
        self.assertEqual(answered.code, "INVALID_CREDENTIALS")

    def test_a_200_without_a_token_is_not_a_login(self):
        # Answering the one field the whole call is for is the contract. Not
        # having it would hand the next step nothing to register with.
        opener, _ = answering(200, {"somethingElse": "?"})

        answered = BackendClient(BASE, opener=opener).login("a@b.com", "abcd1234")

        self.assertFalse(answered.succeeded)
        self.assertEqual(answered.code, "MALFORMED_LOGIN_RESPONSE")

    def test_an_empty_token_is_not_a_login(self):
        opener, _ = answering(200, {"accessToken": "   "})

        self.assertFalse(
            BackendClient(BASE, opener=opener).login("a@b.com", "abcd1234").succeeded)


class WhenTheServiceIsNotThereTest(unittest.TestCase):
    """Unreachable is never a refusal: nothing about the account was learned."""

    def test_a_refused_connection(self):
        opener, _ = answering(raises=urllib.error.URLError("connection refused"))

        answered = BackendClient(BASE, opener=opener).login("a@b.com", "abcd1234")

        self.assertIs(answered.outcome, Outcome.UNREACHABLE)
        self.assertEqual(answered.code, "BACKEND_UNREACHABLE")

    def test_a_timeout(self):
        opener, _ = answering(raises=socket.timeout("timed out"))

        answered = BackendClient(BASE, opener=opener).signup("a@b.com", "abcd1234", "s")

        self.assertIs(answered.outcome, Outcome.UNREACHABLE)

    def test_the_person_is_told_it_is_worth_trying_again(self):
        opener, _ = answering(raises=urllib.error.URLError("down"))

        answered = BackendClient(BASE, opener=opener).login("a@b.com", "abcd1234")

        self.assertIn("Try again", answered.message)

    def test_a_refusal_with_no_readable_body_still_says_something_useful(self):
        # A proxy in front of the Backend, say.
        opener, _ = answering(401)

        answered = BackendClient(BASE, opener=opener).login("a@b.com", "abcd1234")

        self.assertIs(answered.outcome, Outcome.REFUSED)
        self.assertIn("password", answered.message)

    def test_a_body_that_is_not_json_is_not_a_crash(self):
        def opener(request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url, 500, "boom", {}, io.BytesIO(b"<html>oops</html>"))

        answered = BackendClient(BASE, opener=opener).login("a@b.com", "abcd1234")

        self.assertIs(answered.outcome, Outcome.REFUSED)
        self.assertEqual(answered.code, "HTTP_500")


class WhatIsSentAndWhatIsNotTest(unittest.TestCase):
    def test_the_request_is_json_and_bounded_by_a_timeout(self):
        opener, sent = answering(200, {"accessToken": "jwt"})

        BackendClient(BASE, opener=opener).login("a@b.com", "abcd1234")

        self.assertEqual(sent["headers"].get("Content-type"), "application/json")
        self.assertIsNotNone(sent["timeout"])

    def test_no_authorization_header_is_sent_when_there_is_no_token(self):
        # Signing in is how a token is obtained; there is none to send yet.
        opener, sent = answering(200, {"accessToken": "jwt"})

        BackendClient(BASE, opener=opener).login("a@b.com", "abcd1234")

        self.assertNotIn("Authorization", sent["headers"])

    def test_a_bearer_token_is_put_in_the_header(self):
        opener, sent = answering(200, {})

        BackendClient(BASE, opener=opener).request(
            "POST", "/api/workers/authenticate", {"workerId": "w"}, bearer="a-token")

        self.assertEqual(sent["headers"].get("Authorization"), "Bearer a-token")

    def test_the_status_is_kept_because_the_worker_api_is_defined_by_it(self):
        opener, _ = answering(204)

        answered = BackendClient(BASE, opener=opener).request(
            "PUT", "/api/workers/w/gpu", {})

        self.assertTrue(answered.succeeded)
        self.assertEqual(answered.status, 204)

    def test_a_refusal_keeps_the_status_too(self):
        opener, _ = answering(403, {"code": "WORKER_REVOKED", "message": "removed"})

        answered = BackendClient(BASE, opener=opener).request("POST", "/x", {})

        self.assertEqual(answered.status, 403)

    def test_a_request_with_no_payload_sends_no_body(self):
        opener, sent = answering(204)

        BackendClient(BASE, opener=opener).request("PUT", "/api/workers/w/gpu")

        self.assertIsNone(sent["body"])

    def test_a_trailing_slash_on_the_base_url_does_not_double_up(self):
        opener, sent = answering(200, {"accessToken": "jwt"})

        BackendClient(BASE + "/", opener=opener).login("a@b.com", "abcd1234")

        self.assertEqual(sent["url"], BASE + LOGIN_PATH)


if __name__ == "__main__":
    unittest.main()
