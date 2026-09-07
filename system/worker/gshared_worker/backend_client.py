"""Talking to the G-shared Backend over HTTP.

Written against the Backend's **actual** implementation, read from
``back/src/main/java/com/gshared/back``, not from a specification:

* ``POST /api/auth/signup`` -- ``{email, password, nickname}``. On success the
  Backend answers **201 with an empty body**. It does not issue a token, so a
  new account has to log in afterwards.
* ``POST /api/auth/login`` -- ``{email, password}``. This one is **not a
  controller**: Spring Security's ``LoginFilter`` intercepts the path. On
  success it answers **200 with** ``{"accessToken": "..."}`` and sets a
  ``refreshToken`` HttpOnly cookie. The Worker keeps neither for long: see
  :mod:`gshared_worker.worker_auth`.
* Every error answers ``{"code": "...", "message": "..."}`` with the code being
  the Backend enum name -- ``DUPLICATE_EMAIL``, ``INVALID_CREDENTIALS``,
  ``INVALID_INPUT``, ``TOO_MANY_REQUESTS`` and so on. The message is already
  written for a person to read, so it is shown as it comes.

Everything is standard library. The Worker package has no third-party
dependencies and this is not the place to start.
"""

from __future__ import annotations

import json
import logging
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .logging_setup import LOGGER_NAME

_debug = logging.getLogger(LOGGER_NAME)

SIGNUP_PATH = "/api/auth/signup"
LOGIN_PATH = "/api/auth/login"

#: A person is waiting at a form, so this is short. Long enough for a cold
#: Spring Boot to answer, short enough that a wrong address is not a hang.
REQUEST_TIMEOUT_SECONDS = 15

#: The Backend's error body is small. Anything larger is not one.
_MAX_ERROR_BYTES = 64 * 1024


class Outcome(Enum):
    """What the Backend said, in terms the caller can act on."""

    OK = "OK"
    #: The Backend answered and refused. ``code`` says why.
    REFUSED = "REFUSED"
    #: Nothing was reached. **Not** the same as a refusal: retrying later is
    #: the right answer, and nothing about the account is known either way.
    UNREACHABLE = "UNREACHABLE"


@dataclass(frozen=True)
class BackendAnswer:
    outcome: Outcome
    #: The Backend's own error enum name, or None when it did not refuse.
    code: str | None = None
    #: What to show a person. Already written for one by the Backend.
    message: str = ""
    #: Parsed success body, when there was one.
    body: dict[str, Any] | None = None
    #: The HTTP status, when one was received. None means nothing answered.
    #: The Worker API is defined in terms of these, so they are kept.
    status: int | None = None

    @property
    def succeeded(self) -> bool:
        return self.outcome is Outcome.OK


class BackendClient:
    """The Backend calls this branch needs, and no others."""

    def __init__(self, base_url: str, *, opener: Any | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._opener = opener or urllib.request.urlopen

    def signup(self, email: str, password: str, nickname: str) -> BackendAnswer:
        """Create an account. The Backend issues no token here.

        Its answer is 201 with an empty body, so a successful signup is
        followed by a login rather than by being logged in.
        """
        return self._post(SIGNUP_PATH, {
            "email": email, "password": password, "nickname": nickname,
        })

    def login(self, email: str, password: str) -> BackendAnswer:
        """Sign in. On success the body carries ``accessToken``."""
        answered = self._post(LOGIN_PATH, {"email": email, "password": password})
        if not answered.succeeded:
            return answered
        token = (answered.body or {}).get("accessToken")
        if not isinstance(token, str) or not token.strip():
            # The Backend answered 200 without the one field the whole call is
            # for. Treating that as a login would hand the next step nothing.
            return BackendAnswer(
                Outcome.REFUSED, "MALFORMED_LOGIN_RESPONSE",
                "The service signed in but returned no access token.",
            )
        return answered

    def _post(self, path: str, payload: dict[str, Any]) -> BackendAnswer:
        return self.request("POST", path, payload)

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        bearer: str | None = None,
    ) -> BackendAnswer:
        """One request, one answer. Every Backend call in the Worker goes here.

        ``bearer`` is whatever the endpoint authenticates with -- a user's
        access token when registering a machine, the Worker's own token
        afterwards. It is put in the header and **never** logged: the whole
        point of the header is that it is the credential.
        """
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            data=None if payload is None else json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                **({"Authorization": f"Bearer {bearer}"} if bearer else {}),
            },
            method=method,
        )
        # Only the method and the path are logged. The body carries a password
        # and the header carries a token.
        _debug.info("backend: %s %s", method, path)
        try:
            with self._opener(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                answered = BackendAnswer(
                    Outcome.OK, body=_read_json(response), message="",
                    status=_status_of(response),
                )
        except urllib.error.HTTPError as refused:
            answered = _refusal(refused)
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as error:
            # A wrong address, a service that is down, a network that is not
            # there. None of them say anything about the account.
            _debug.info("backend: %s could not be reached (%s)", path, type(error).__name__)
            return BackendAnswer(
                Outcome.UNREACHABLE, "BACKEND_UNREACHABLE",
                "The G-shared service could not be reached. Try again when it is back.",
            )
        _debug.info(
            "backend: %s -> %s%s", path, answered.outcome.value,
            f" ({answered.code})" if answered.code else "",
        )
        return answered


def _refusal(refused: urllib.error.HTTPError) -> BackendAnswer:
    """Turn the Backend's error body into something to show a person."""
    body = _read_json(refused) or {}
    code = body.get("code")
    message = body.get("message")
    if not isinstance(code, str) or not code:
        code = f"HTTP_{refused.code}"
    if not isinstance(message, str) or not message:
        message = _WITHOUT_A_BODY.get(refused.code, "The request was refused.")
    return BackendAnswer(Outcome.REFUSED, code, message, status=refused.code)


#: What to say when the Backend refused without a readable body -- a proxy in
#: front of it, say. The Backend's own messages are preferred whenever there.
_WITHOUT_A_BODY = {
    400: "The details were not accepted.",
    401: "The email or password is not correct.",
    403: "This account is not allowed to do that.",
    409: "That is already taken.",
    429: "Too many attempts. Wait a few minutes and try again.",
    500: "The service had a problem. Try again in a moment.",
}


def _status_of(response: Any) -> int | None:
    """The status a success answered with. 204 and 200 mean different things."""
    status = getattr(response, "status", None) or getattr(response, "code", None)
    return int(status) if isinstance(status, int) else None


def _read_json(response: Any) -> dict[str, Any] | None:
    """The body as an object, or None when there is not one.

    An empty body is normal here: a successful signup answers 201 with nothing
    in it and a GPU update answers 204, and that is a success rather than a
    parse failure.
    """
    try:
        raw = response.read(_MAX_ERROR_BYTES)
    except Exception:
        return None
    if not raw:
        return None
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None
