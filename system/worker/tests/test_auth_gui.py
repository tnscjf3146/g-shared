"""The sign-in screens, driven without opening a window.

The presenter holds every decision, so this file covers the whole of what the
GUI does. The window itself is checked once, for real, in the smoke test at the
bottom -- which is skipped where there is no display to open one on.
"""

from __future__ import annotations

import os
import unittest

from gshared_worker.backend_client import BackendAnswer, Outcome
from gshared_worker.gui.presenter import AuthPresenter, Screen


class Backend:
    """Answers signup and login as a test says, and records what it was sent."""

    def __init__(self, login=None, signup=None):
        self._login = login or BackendAnswer(Outcome.OK, body={"accessToken": "jwt"})
        self._signup = signup or BackendAnswer(Outcome.OK)
        self.logins: list[tuple] = []
        self.signups: list[tuple] = []

    def login(self, email, password):
        self.logins.append((email, password))
        return self._login

    def signup(self, email, password, nickname):
        self.signups.append((email, password, nickname))
        return self._signup


def refusal(code, message):
    return BackendAnswer(Outcome.REFUSED, code, message)


def unreachable():
    return BackendAnswer(
        Outcome.UNREACHABLE, "BACKEND_UNREACHABLE",
        "The G-shared service could not be reached. Try again when it is back.")


class WhereItStartsTest(unittest.TestCase):
    def test_it_opens_on_the_sign_in_screen(self):
        self.assertIs(AuthPresenter(Backend()).screen, Screen.LOGIN)

    def test_somebody_without_an_account_can_get_to_the_other_screen(self):
        presenter = AuthPresenter(Backend())

        shown = presenter.show_signup()

        self.assertIs(shown.screen, Screen.SIGNUP)
        self.assertIs(presenter.screen, Screen.SIGNUP)

    def test_and_back_again(self):
        presenter = AuthPresenter(Backend())
        presenter.show_signup()

        self.assertIs(presenter.show_login().screen, Screen.LOGIN)


class SigningInTest(unittest.TestCase):
    def test_a_good_password_hands_back_the_access_token(self):
        backend = Backend()

        shown = AuthPresenter(backend).log_in("a@b.com", "abcd1234")

        self.assertTrue(shown.signed_in)
        self.assertEqual(shown.access_token, "jwt")

    def test_the_email_is_trimmed_but_the_password_is_not_touched(self):
        backend = Backend()

        AuthPresenter(backend).log_in("  a@b.com  ", " abcd1234 ")

        self.assertEqual(backend.logins, [("a@b.com", " abcd1234 ")])

    def test_a_wrong_password_is_shown_in_the_backends_own_words(self):
        backend = Backend(login=refusal(
            "INVALID_CREDENTIALS", "아이디 또는 비밀번호가 올바르지 않습니다."))

        shown = AuthPresenter(backend).log_in("a@b.com", "wrong")

        self.assertFalse(shown.signed_in)
        self.assertIn("올바르지 않습니다", shown.error)

    def test_a_service_that_is_down_says_so_rather_than_blaming_the_password(self):
        backend = Backend(login=unreachable())

        shown = AuthPresenter(backend).log_in("a@b.com", "abcd1234")

        self.assertIn("could not be reached", shown.error)

    def test_an_empty_field_is_answered_without_sending_anything(self):
        # No reason to send an empty field over the network to be told it is
        # empty -- and no reason to spend a rate-limit attempt on it.
        backend = Backend()

        for email, password in (("", "abcd1234"), ("a@b.com", ""), ("   ", "abcd1234")):
            shown = AuthPresenter(backend).log_in(email, password)
            self.assertTrue(shown.error)
            self.assertFalse(shown.signed_in)
        self.assertEqual(backend.logins, [])

    def test_a_failed_sign_in_stays_on_the_sign_in_screen(self):
        backend = Backend(login=refusal("INVALID_CREDENTIALS", "no"))

        self.assertIs(
            AuthPresenter(backend).log_in("a@b.com", "x").screen, Screen.LOGIN)


class SigningUpTest(unittest.TestCase):
    def test_it_sends_the_three_fields_the_backend_requires(self):
        backend = Backend()

        AuthPresenter(backend).sign_up("a@b.com", "abcd1234", "sdk")

        self.assertEqual(backend.signups, [("a@b.com", "abcd1234", "sdk")])

    def test_a_new_account_lands_on_the_sign_in_screen(self):
        # The Backend answers 201 with no token, so there is nothing to be
        # signed in with. Pretending otherwise would be a guess.
        backend = Backend()

        shown = AuthPresenter(backend).sign_up("a@b.com", "abcd1234", "sdk")

        self.assertIs(shown.screen, Screen.LOGIN)
        self.assertFalse(shown.signed_in)
        self.assertIn("로그인", shown.notice)

    def test_nobody_is_signed_in_automatically(self):
        backend = Backend()

        AuthPresenter(backend).sign_up("a@b.com", "abcd1234", "sdk")

        self.assertEqual(backend.logins, [])

    def test_a_taken_email_is_shown_in_the_backends_own_words(self):
        backend = Backend(signup=refusal(
            "DUPLICATE_EMAIL", "이미 사용중인 이메일입니다."))

        shown = AuthPresenter(backend).sign_up("a@b.com", "abcd1234", "sdk")

        self.assertIs(shown.screen, Screen.SIGNUP)
        self.assertIn("이미 사용중인", shown.error)

    def test_a_password_the_backend_rejects_is_shown_as_the_backend_put_it(self):
        backend = Backend(signup=refusal(
            "INVALID_INPUT", "비밀번호는 6 ~ 20자입니다."))

        shown = AuthPresenter(backend).sign_up("a@b.com", "x", "sdk")

        self.assertIn("6 ~ 20", shown.error)

    def test_a_service_that_is_down_keeps_them_on_the_form(self):
        backend = Backend(signup=unreachable())

        shown = AuthPresenter(backend).sign_up("a@b.com", "abcd1234", "sdk")

        self.assertIs(shown.screen, Screen.SIGNUP)
        self.assertIn("could not be reached", shown.error)

    def test_an_empty_field_is_answered_without_sending_anything(self):
        backend = Backend()

        for fields in (("", "abcd1234", "sdk"), ("a@b.com", "", "sdk"),
                       ("a@b.com", "abcd1234", "  ")):
            self.assertTrue(AuthPresenter(backend).sign_up(*fields).error)
        self.assertEqual(backend.signups, [])


class NothingIsKeptTest(unittest.TestCase):
    def test_the_presenter_never_holds_the_password(self):
        presenter = AuthPresenter(Backend())

        presenter.log_in("a@b.com", "a-secret-password")

        self.assertNotIn(
            "a-secret-password", repr(vars(presenter)))

    def test_what_is_shown_never_carries_the_password(self):
        shown = AuthPresenter(Backend()).log_in("a@b.com", "a-secret-password")

        self.assertNotIn("a-secret-password", f"{shown.error}{shown.notice}")

    def test_the_presenter_never_holds_the_access_token(self):
        # It is handed back once and is the caller's from then on.
        presenter = AuthPresenter(Backend())

        presenter.log_in("a@b.com", "abcd1234")

        self.assertNotIn("jwt", repr(vars(presenter)))


@unittest.skipUnless(os.name == "nt", "the sign-in window is drawn on Windows")
class TheWindowItselfTest(unittest.TestCase):
    """One real Tk window, built and torn down without a person touching it."""

    def setUp(self):
        try:
            import tkinter
            self._root = tkinter.Tk()
        except Exception as error:
            self.skipTest(f"no display to open a window on: {error}")
        self._root.withdraw()
        self.addCleanup(self._root.destroy)

    def test_both_screens_can_be_drawn(self):
        from gshared_worker.gui.window import AuthWindow

        window = AuthWindow(AuthPresenter(Backend()))
        # Draw into a frame of our own rather than opening a second Tk root.
        import tkinter.ttk as ttk
        window._body = ttk.Frame(self._root)
        window._render(window._presenter.show_login())
        drawn_login = len(window._body.winfo_children())
        window._render(window._presenter.show_signup())
        drawn_signup = len(window._body.winfo_children())

        self.assertGreater(drawn_login, 0)
        self.assertGreater(drawn_signup, drawn_login)

    def test_the_password_field_is_masked(self):
        import tkinter.ttk as ttk

        from gshared_worker.gui.window import AuthWindow

        window = AuthWindow(AuthPresenter(Backend()))
        window._body = ttk.Frame(self._root)
        window._render(window._presenter.show_login())

        masked = [
            child for child in window._body.winfo_children()
            if isinstance(child, ttk.Entry) and child.cget("show") == "*"
        ]
        self.assertEqual(len(masked), 1)


if __name__ == "__main__":
    unittest.main()
