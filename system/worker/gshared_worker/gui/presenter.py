"""What happens when somebody presses a button. No Tk anywhere in this file.

Keeping this apart from the window is what makes the sign-in flow testable
without opening one -- and it is also the boundary that stops UI code from
growing its own HTTP calls, its own credential handling, or its own idea of
what a Worker's state is. The presenter calls services and reports what they
said.

**The password is never held.** It goes from the field into one call and is not
kept on this object, not logged, and not written anywhere.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from .. import backend_client
from ..logging_setup import LOGGER_NAME

_debug = logging.getLogger(LOGGER_NAME)


class Screen(Enum):
    LOGIN = "LOGIN"
    SIGNUP = "SIGNUP"


@dataclass(frozen=True)
class Shown:
    """What the window should display after an action."""

    screen: Screen
    #: Something went wrong and the person has to act on it.
    error: str = ""
    #: Something went right and is worth saying.
    notice: str = ""
    #: The person is signed in; the window may close.
    access_token: str | None = None

    @property
    def signed_in(self) -> bool:
        return self.access_token is not None


class AuthPresenter:
    """Sign-in and sign-up, as two screens and four things a person can do."""

    def __init__(self, client: backend_client.BackendClient) -> None:
        self._client = client
        self.screen = Screen.LOGIN

    # -- moving between the two screens -------------------------------------

    def show_signup(self) -> Shown:
        self.screen = Screen.SIGNUP
        return Shown(Screen.SIGNUP)

    def show_login(self) -> Shown:
        self.screen = Screen.LOGIN
        return Shown(Screen.LOGIN)

    # -- the two things that talk to the Backend ----------------------------

    def log_in(self, email: str, password: str) -> Shown:
        missing = _missing_login(email, password)
        if missing:
            # Answered here rather than by the Backend: there is no reason to
            # send an empty field over the network to be told it is empty.
            return Shown(Screen.LOGIN, error=missing)

        answered = self._client.login(email.strip(), password)
        if answered.succeeded:
            _debug.debug("gui: signed in")
            return Shown(Screen.LOGIN, access_token=answered.body["accessToken"])
        return Shown(Screen.LOGIN, error=answered.message)

    def sign_up(self, email: str, password: str, nickname: str) -> Shown:
        missing = _missing_signup(email, password, nickname)
        if missing:
            return Shown(Screen.SIGNUP, error=missing)

        answered = self._client.signup(email.strip(), password, nickname.strip())
        if not answered.succeeded:
            return Shown(Screen.SIGNUP, error=answered.message)

        # The Backend answers 201 with an empty body and issues no token, so
        # there is nothing to be signed in with. Sending the person to the
        # login screen is what actually happened, rather than a guess that
        # they are now logged in.
        _debug.debug("gui: account created")
        self.screen = Screen.LOGIN
        return Shown(
            Screen.LOGIN,
            notice="계정을 만들었습니다. 이제 로그인하세요.",
        )


def _missing_login(email: str, password: str) -> str:
    if not email.strip():
        return "이메일을 입력하세요."
    if not password:
        return "비밀번호를 입력하세요."
    return ""


def _missing_signup(email: str, password: str, nickname: str) -> str:
    if not email.strip():
        return "이메일을 입력하세요."
    if not password:
        return "비밀번호를 입력하세요."
    if not nickname.strip():
        return "닉네임을 입력하세요."
    return ""
