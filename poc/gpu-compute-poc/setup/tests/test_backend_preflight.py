"""Tests for WSS credential preflight and its TLS/security boundary."""

from __future__ import annotations

from pathlib import Path
import ssl
from tempfile import TemporaryDirectory
import unittest

from gpu_worker_setup.backend import BackendPreflightClient
from gpu_worker_setup.credentials import CredentialInput
from gpu_worker_setup.endpoint_config import BackendEndpointConfig
from gpu_worker_setup.secure_logging import SecureEventLogger

from tests.tls_server import TlsWebSocketServer, let_server_observe_close


class _CapturingConnection:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self.closed = True


class _CapturingConnector:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __call__(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return _CapturingConnection()


def _credentials(base_url: str, *, worker_id: str = "worker-001") -> CredentialInput:
    return CredentialInput(
        backend_url=base_url,
        worker_id=worker_id,
        worker_token="test-only-token",
    )


class BackendPreflightUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_handshake_uses_only_additional_authorization_header(self) -> None:
        credentials = _credentials("https://api.example.com/gpu")
        endpoints = BackendEndpointConfig.from_base_url(credentials.backend_url)
        connector = _CapturingConnector()

        result = await BackendPreflightClient(
            endpoints, credentials, connector=connector
        ).validate()

        self.assertTrue(result.accepted)
        self.assertEqual(len(connector.calls), 1)
        url, kwargs = connector.calls[0]
        self.assertEqual(url, endpoints.worker_websocket_url)
        self.assertEqual(
            kwargs["additional_headers"],
            {"Authorization": "Bearer test-only-token"},
        )
        self.assertNotIn("test-only-token", url)
        self.assertEqual(set(kwargs), {"additional_headers", "open_timeout"})

    async def test_invalid_worker_id_is_rejected_before_network_access(self) -> None:
        invalid_values = ("", " worker", "worker/one", "worker@one", "a" * 65)

        for worker_id in invalid_values:
            connector = _CapturingConnector()
            credentials = _credentials(
                "https://api.example.com", worker_id=worker_id or "placeholder"
            )
            if not worker_id:
                object.__setattr__(credentials, "worker_id", worker_id)
            endpoints = BackendEndpointConfig.from_base_url(credentials.backend_url)
            with self.subTest(worker_id=worker_id):
                result = await BackendPreflightClient(
                    endpoints, credentials, connector=connector
                ).validate()
                self.assertFalse(result.accepted)
                self.assertEqual(result.error_code, "INVALID_WORKER_ID")
                self.assertEqual(connector.calls, [])

    async def test_non_tls_endpoint_is_rejected_before_network_access(self) -> None:
        credentials = _credentials("http://localhost:8080")
        endpoints = BackendEndpointConfig.from_development_base_url(
            credentials.backend_url
        )
        connector = _CapturingConnector()

        result = await BackendPreflightClient(
            endpoints, credentials, connector=connector
        ).validate()

        self.assertFalse(result.accepted)
        self.assertEqual(result.error_code, "INVALID_BACKEND_URL")
        self.assertEqual(connector.calls, [])

    async def test_injected_tls_context_cannot_disable_verification(self) -> None:
        credentials = _credentials("https://api.example.com")
        endpoints = BackendEndpointConfig.from_base_url(credentials.backend_url)
        insecure_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        insecure_context.check_hostname = False
        insecure_context.verify_mode = ssl.CERT_NONE

        with self.assertRaises(ValueError):
            BackendPreflightClient(
                endpoints,
                credentials,
                ssl_context=insecure_context,
            )


class BackendPreflightTlsTests(unittest.IsolatedAsyncioTestCase):
    async def test_trusted_certificate_and_token_succeed_without_register(self) -> None:
        async with TlsWebSocketServer(token="test-only-token") as server:
            credentials = _credentials(server.base_url)
            endpoints = BackendEndpointConfig.from_base_url(credentials.backend_url)

            result = await BackendPreflightClient(
                endpoints,
                credentials,
                ssl_context=server.trusted_client_context(),
            ).validate()
            await let_server_observe_close()

            self.assertTrue(result.accepted)
            self.assertEqual(
                server.authorization_values, ["Bearer test-only-token"]
            )
            self.assertEqual(server.messages, [])

    async def test_closed_endpoint_reports_only_unreachable_without_detail(self) -> None:
        async with TlsWebSocketServer(token="test-only-token") as server:
            base_url = server.base_url
            trusted_context = server.trusted_client_context()
        credentials = _credentials(base_url)
        endpoints = BackendEndpointConfig.from_base_url(credentials.backend_url)

        result = await BackendPreflightClient(
            endpoints,
            credentials,
            ssl_context=trusted_context,
            open_timeout=2.0,
        ).validate()

        self.assertFalse(result.accepted)
        self.assertEqual(result.error_code, "BACKEND_UNREACHABLE")

    async def test_untrusted_certificate_is_rejected_by_default_validation(self) -> None:
        async with TlsWebSocketServer(token="test-only-token") as server:
            credentials = _credentials(server.base_url)
            endpoints = BackendEndpointConfig.from_base_url(credentials.backend_url)

            result = await BackendPreflightClient(endpoints, credentials).validate()

            self.assertFalse(result.accepted)
            self.assertEqual(result.error_code, "BACKEND_UNREACHABLE")

    async def test_unauthorized_and_forbidden_handshakes_reject_token_safely(self) -> None:
        for status in (401, 403):
            with self.subTest(status=status):
                async with TlsWebSocketServer(
                    token="test-only-token", rejection_status=status
                ) as server:
                    credentials = _credentials(server.base_url)
                    endpoints = BackendEndpointConfig.from_base_url(
                        credentials.backend_url
                    )
                    result = await BackendPreflightClient(
                        endpoints,
                        credentials,
                        ssl_context=server.trusted_client_context(),
                    ).validate()

                    self.assertFalse(result.accepted)
                    self.assertEqual(result.error_code, "WORKER_TOKEN_REJECTED")

    async def test_silent_non_upgrade_response_blames_the_token_not_the_network(self) -> None:
        async with TlsWebSocketServer(
            token="test-only-token", refuse_upgrade_silently=True
        ) as server:
            credentials = _credentials(server.base_url)
            endpoints = BackendEndpointConfig.from_base_url(credentials.backend_url)

            result = await BackendPreflightClient(
                endpoints,
                credentials,
                ssl_context=server.trusted_client_context(),
            ).validate()

            self.assertFalse(result.accepted)
            self.assertEqual(result.error_code, "WORKER_TOKEN_REJECTED")

    async def test_absent_endpoint_is_a_url_problem_not_a_token_problem(self) -> None:
        async with TlsWebSocketServer(
            token="test-only-token", rejection_status=404
        ) as server:
            credentials = _credentials(server.base_url)
            endpoints = BackendEndpointConfig.from_base_url(credentials.backend_url)

            result = await BackendPreflightClient(
                endpoints,
                credentials,
                ssl_context=server.trusted_client_context(),
            ).validate()

            self.assertFalse(result.accepted)
            self.assertEqual(result.error_code, "INVALID_BACKEND_URL")

    async def test_server_error_stays_a_transport_problem(self) -> None:
        async with TlsWebSocketServer(
            token="test-only-token", rejection_status=503
        ) as server:
            credentials = _credentials(server.base_url)
            endpoints = BackendEndpointConfig.from_base_url(credentials.backend_url)

            result = await BackendPreflightClient(
                endpoints,
                credentials,
                ssl_context=server.trusted_client_context(),
            ).validate()

            self.assertFalse(result.accepted)
            self.assertEqual(result.error_code, "BACKEND_UNREACHABLE")

    async def test_rejection_logging_never_contains_header_token_or_response_body(self) -> None:
        with TemporaryDirectory() as directory:
            log_path = Path(directory) / "setup.log"
            logger = SecureEventLogger(
                log_path, timestamp_factory=lambda: "2026-09-01T12:00:00Z"
            )
            async with TlsWebSocketServer(
                token="test-only-token", rejection_status=401
            ) as server:
                credentials = _credentials(server.base_url)
                endpoints = BackendEndpointConfig.from_base_url(
                    credentials.backend_url
                )

                result = await BackendPreflightClient(
                    endpoints,
                    credentials,
                    ssl_context=server.trusted_client_context(),
                    logger=logger,
                ).validate()

            text = log_path.read_text(encoding="utf-8")
            self.assertEqual(result.error_code, "WORKER_TOKEN_REJECTED")
            self.assertNotIn("test-only-token", text)
            self.assertNotIn("Authorization", text)
            self.assertNotIn("raw-response-body-must-remain-private", text)


if __name__ == "__main__":
    unittest.main()
