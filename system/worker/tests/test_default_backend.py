"""Where a Worker nobody configured connects, and how every address is built.

One setting names the service. Signing in and registering go to its HTTPS
origin; the Worker socket goes to ``wss://`` on the same host with the path the
Backend registered. Whatever form the setting takes -- ``https://``, ``wss://``,
with or without a trailing slash or the socket path -- the same two addresses
come out, so no API path is ever built as ``//api/...``.
"""

from __future__ import annotations

import unittest

from gshared_worker.auth_check import _http_origin_of
from gshared_worker.backend_client import LOGIN_PATH, BackendClient
from gshared_worker.config import DEFAULT_BACKEND_URL, ENV_BACKEND_URL, resolve_settings
from gshared_worker.worker_socket import WS_PATH, websocket_url

SSAFY = "https://j15e204.p.ssafy.io"


class TheDefaultTest(unittest.TestCase):
    def test_it_is_the_ssafy_deployment(self):
        self.assertEqual(DEFAULT_BACKEND_URL, SSAFY)

    def test_no_older_deployment_is_left_in_it(self):
        for old in ("railway", "gpu-compute-poc", "localhost"):
            self.assertNotIn(old, DEFAULT_BACKEND_URL)

    def test_signing_in_goes_to_its_https_origin(self):
        self.assertEqual(_http_origin_of(DEFAULT_BACKEND_URL), SSAFY)

    def test_the_socket_goes_to_wss_on_the_same_host(self):
        self.assertEqual(websocket_url(DEFAULT_BACKEND_URL),
                         "wss://j15e204.p.ssafy.io" + WS_PATH)

    def test_the_socket_path_is_the_one_the_backend_registered(self):
        # WorkerWebSocketConfig registers exactly this path.
        self.assertEqual(WS_PATH, "/ws/workers")

    def test_a_worker_with_no_settings_uses_it(self):
        settings = resolve_settings(env={}, directory=_empty())

        self.assertEqual(settings.backend_url, SSAFY)


class EveryFormOfTheSettingTest(unittest.TestCase):
    FORMS = (
        "https://j15e204.p.ssafy.io",
        "https://j15e204.p.ssafy.io/",
        "wss://j15e204.p.ssafy.io",
        "wss://j15e204.p.ssafy.io/ws/workers",
        "wss://j15e204.p.ssafy.io/ws/workers/",
    )

    def test_each_gives_the_same_https_origin(self):
        for form in self.FORMS:
            with self.subTest(form=form):
                self.assertEqual(_http_origin_of(form), SSAFY)

    def test_each_gives_the_same_socket_address(self):
        for form in self.FORMS:
            with self.subTest(form=form):
                self.assertEqual(websocket_url(form), "wss://j15e204.p.ssafy.io/ws/workers")

    def test_an_api_path_never_gets_a_double_slash(self):
        sent = {}

        def opener(request, timeout=None):
            sent["url"] = request.full_url
            raise OSError("not sent anywhere in a test")

        for form in self.FORMS + (SSAFY + "//",):
            with self.subTest(form=form):
                BackendClient(_http_origin_of(form), opener=opener).login("a@b.com", "x1234567")
                self.assertEqual(sent["url"], SSAFY + LOGIN_PATH)
                self.assertNotIn("//api", sent["url"])


class AnOverrideStillWinsTest(unittest.TestCase):
    def test_the_environment_variable_is_used_instead(self):
        settings = resolve_settings(
            env={ENV_BACKEND_URL: "http://localhost:8080"}, directory=_empty())

        self.assertEqual(settings.backend_url, "http://localhost:8080")
        self.assertEqual(websocket_url(settings.backend_url), "ws://localhost:8080/ws/workers")
        self.assertEqual(_http_origin_of(settings.backend_url), "http://localhost:8080")

    def test_the_configuration_file_is_used_instead(self):
        import json

        directory = _empty()
        (directory / "worker-config.json").write_text(
            json.dumps({"backendUrl": "https://staging.example.com/"}), encoding="utf-8")

        settings = resolve_settings(env={}, directory=directory)

        self.assertEqual(settings.backend_url, "https://staging.example.com/")
        self.assertEqual(_http_origin_of(settings.backend_url), "https://staging.example.com")
        self.assertEqual(websocket_url(settings.backend_url),
                         "wss://staging.example.com/ws/workers")


def _empty():
    """A settings folder of our own, removed when the test run ends."""
    import atexit
    import shutil
    import tempfile
    from pathlib import Path

    path = Path(tempfile.mkdtemp())
    atexit.register(shutil.rmtree, path, True)
    return path


if __name__ == "__main__":
    unittest.main()
