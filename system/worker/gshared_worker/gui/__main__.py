"""Open the real sign-in window on its own: ``python -m gshared_worker.gui``.

The Worker's own run reaches this window only when it can finish what the
window starts, and today it cannot -- registering a machine has to state a
benchmark figure nothing measures yet. That is the right behaviour for a
supplier, who should not be asked for a password that could not be used.

It is the wrong behaviour for looking at the screens. So this opens them
directly, against the Backend the Worker is configured for. Signing in really
does sign in; what it will not do is register this machine, and it prints
whether it got a token rather than the token.
"""

from __future__ import annotations

import os

from ..auth_check import _http_origin_of
from ..backend_client import BackendClient
from ..config import data_directory, resolve_backend_url
from ..errors import WorkerConfigurationError
from ..logging_setup import start_debug_log
from .presenter import AuthPresenter
from .window import AuthWindow


def main() -> int:
    try:
        # The address only. A machine with no workerId yet is the normal case
        # here -- obtaining one is what this window is for.
        backend_url = resolve_backend_url(env=os.environ)
    except WorkerConfigurationError as error:
        print(f"G-shared cannot read this configuration: {error}")
        return 2

    origin = _http_origin_of(backend_url)
    log_path = start_debug_log(data_directory())
    print(f"Signing in against {origin}")
    print(f"  What happens is written to {log_path}.")
    print("  This window signs in only. It does not register this machine.")

    access_token = AuthWindow(AuthPresenter(BackendClient(origin))).ask_to_sign_in()

    if access_token:
        # Said, not shown. The token is the credential.
        print("Signed in. An access token was issued.")
        return 0
    print("Closed without signing in.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
