"""The sign-in window. Tk only -- every decision belongs to the presenter.

tkinter rather than a framework: it ships with Python, so the Worker keeps its
empty dependency list; PyInstaller bundles it without a hook; and what is
needed here is two forms and a message. Anything larger would be more download
for a supplier and more to go wrong in the build than the screen is worth.

The window is opened only when there is no Worker identity on this machine. A
registered Worker never sees it.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .presenter import AuthPresenter, Screen, Shown

_TITLE = "G-shared"
_PAD = {"padx": 16, "pady": 4}


class AuthWindow:
    """Shows the two screens and returns the access token, or None.

    The token is handed back and not kept: closing the window leaves nothing
    behind that could be read later.
    """

    def __init__(self, presenter: AuthPresenter) -> None:
        self._presenter = presenter
        self._token: str | None = None
        self._root: tk.Tk | None = None

    def ask_to_sign_in(self) -> str | None:
        """Open the window and block until it is closed or sign-in succeeds."""
        self._root = tk.Tk()
        self._root.title(_TITLE)
        self._root.resizable(False, False)
        self._body = ttk.Frame(self._root)
        self._body.pack(fill="both", expand=True, pady=(12, 16))
        self._render(Shown(Screen.LOGIN))
        self._root.mainloop()
        return self._token

    # -- drawing -------------------------------------------------------------

    def _render(self, shown: Shown) -> None:
        if shown.signed_in:
            self._token = shown.access_token
            self._close()
            return

        for child in self._body.winfo_children():
            child.destroy()

        if shown.screen is Screen.SIGNUP:
            self._draw_signup(shown)
        else:
            self._draw_login(shown)

    def _draw_login(self, shown: Shown) -> None:
        self._heading("G-shared")
        email = self._field("Email")
        password = self._field("Password", secret=True)
        self._message(shown)
        self._button("Sign in", lambda: self._render(
            self._presenter.log_in(email.get(), password.get())))
        self._footer("No account yet?", "Sign up",
                     lambda: self._render(self._presenter.show_signup()))
        email.focus_set()

    def _draw_signup(self, shown: Shown) -> None:
        self._heading("Create a G-shared account")
        email = self._field("Email")
        password = self._field("Password", secret=True)
        nickname = self._field("Nickname")
        self._message(shown)
        self._button("Create account", lambda: self._render(
            self._presenter.sign_up(email.get(), password.get(), nickname.get())))
        self._footer("Already have one?", "Sign in",
                     lambda: self._render(self._presenter.show_login()))
        email.focus_set()

    # -- the few widgets this needs -----------------------------------------

    def _heading(self, text: str) -> None:
        ttk.Label(self._body, text=text, font=("Segoe UI", 14, "bold")).pack(
            anchor="w", **_PAD)

    def _field(self, label: str, *, secret: bool = False) -> ttk.Entry:
        ttk.Label(self._body, text=label).pack(anchor="w", padx=16)
        # show="*" is the masking. The value is read once, on the button press.
        entry = ttk.Entry(self._body, width=34, show="*" if secret else "")
        entry.pack(anchor="w", **_PAD)
        return entry

    def _message(self, shown: Shown) -> None:
        text = shown.error or shown.notice
        if not text:
            return
        ttk.Label(
            self._body, text=text, wraplength=280,
            foreground="#b00020" if shown.error else "#006400",
        ).pack(anchor="w", **_PAD)

    def _button(self, text: str, command) -> None:
        ttk.Button(self._body, text=text, command=command).pack(
            anchor="w", padx=16, pady=(8, 4))

    def _footer(self, question: str, action: str, command) -> None:
        row = ttk.Frame(self._body)
        row.pack(anchor="w", padx=16, pady=(8, 0))
        ttk.Label(row, text=question).pack(side="left")
        link = ttk.Label(row, text=f" {action}", foreground="#0645ad", cursor="hand2")
        link.pack(side="left")
        link.bind("<Button-1>", lambda _event: command())

    def _close(self) -> None:
        if self._root is not None:
            self._root.destroy()
            self._root = None
