"""Tests for structured Backend endpoint construction."""

from __future__ import annotations

import unittest

from gpu_worker_setup.endpoint_config import BackendEndpointConfig


class BackendEndpointConfigTests(unittest.TestCase):
    def test_production_endpoint_builds_explicit_wss_and_rest_urls(self) -> None:
        config = BackendEndpointConfig.from_base_url("https://api.example.com/gpu")

        self.assertEqual(
            config.worker_websocket_url, "wss://api.example.com/gpu/ws/workers"
        )
        self.assertEqual(
            config.worker_status_url("worker a"),
            "https://api.example.com/gpu/api/workers/worker%20a",
        )

    def test_production_preserves_port_and_reverse_proxy_path(self) -> None:
        config = BackendEndpointConfig.from_base_url("https://api.example.com:8443/v1/")

        self.assertEqual(
            config.worker_websocket_url,
            "wss://api.example.com:8443/v1/ws/workers",
        )
        self.assertEqual(
            config.worker_status_url("worker/one"),
            "https://api.example.com:8443/v1/api/workers/worker%2Fone",
        )

    def test_base_path_is_emitted_as_a_valid_normalized_url_path(self) -> None:
        config = BackendEndpointConfig.from_base_url(
            "https://api.example.com/gpu%20cluster"
        )

        self.assertEqual(
            config.worker_websocket_url,
            "wss://api.example.com/gpu%20cluster/ws/workers",
        )

    def test_production_rejects_unsafe_or_non_tls_base_urls(self) -> None:
        invalid_urls = (
            "http://api.example.com",
            "https://user:password@api.example.com",
            "https://api.example.com/gpu?debug=1",
            "https://api.example.com/gpu#fragment",
            "https://api.example.com/gpu/../admin",
            "https://api.example.com/gpu/%2e%2e/admin",
            "https://api.example.com /gpu",
            "https://api.example.com/\tcontrol",
            "https://api.example.com/\ncontrol",
        )

        for value in invalid_urls:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    BackendEndpointConfig.from_base_url(value)

    def test_development_profile_is_explicit_for_local_http_only(self) -> None:
        config = BackendEndpointConfig.from_development_base_url(
            "http://127.0.0.1:8000/base"
        )

        self.assertEqual(
            config.worker_websocket_url, "ws://127.0.0.1:8000/base/ws/workers"
        )


if __name__ == "__main__":
    unittest.main()
