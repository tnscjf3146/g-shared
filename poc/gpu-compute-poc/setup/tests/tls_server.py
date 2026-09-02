"""Local TLS WebSocket server used only by Backend boundary tests."""

from __future__ import annotations

import asyncio
from http import HTTPStatus
from pathlib import Path
import ssl
from tempfile import TemporaryDirectory

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed


_CERTIFICATE = """-----BEGIN CERTIFICATE-----
MIIC0zCCAbugAwIBAgIIDq+6blnPRKcwDQYJKoZIhvcNAQELBQAwFDESMBAGA1UE
AxMJbG9jYWxob3N0MB4XDTI2MDgzMTA0MjEzMVoXDTM2MDkwMTA0MjEzMVowFDES
MBAGA1UEAxMJbG9jYWxob3N0MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKC
AQEAtadZn04+UDfnvgnG5KrbEINRdh3c44P+K34QJTQXlM3mwJwzpTjOl9ckq2sx
0H+wOIj1nMe2hKyowkqnY1UxVpJbVeT7wUkQ2iyb27KXJRfNu+SScHKk95OomOPL
Xi3VaVq78NTh9QVIIjyqel0Ad1nuXMV3csIAIC8JlD6cw3RIpr112aD9vwLM8BBY
3p/TAysauNXCdSNCMQrnLCcMHFORfUSzxMN6euVjDAg4h55OmfiwarnSU7zSPAXq
EwbqMBd+emmkwFizwjhjxR2Q6JvXfU6T3YUqW7gLF5MERHrLMmSwBXxIxaJEpAPT
t0QHmlGACyLY+W65ilzkCJd2OQIDAQABoykwJzAaBgNVHREEEzARgglsb2NhbGhv
c3SHBH8AAAEwCQYDVR0TBAIwADANBgkqhkiG9w0BAQsFAAOCAQEAArNV7+FM784L
tjwITsp10t5BMXbUOLAt2s/0qaxrCtJcvHoLHZoQRSLIUqaXwmPtpt1koFUIIssZ
SFghptk6y88drgdzJ3TayBhUM3Q6o96LCvIHviKCfowy01XL7pVMcc4jsX5zgjd2
O8s3poIFVEig++E2OpeTFHDDWy6puAe/dY2qGgrCyftRY8qwGZRplX1vF78arCwG
IMxeuqiVSqWZTg4VKNHnmyA9m+kppVM2Y1887StUX0Jqn72gs7/GPCP+MudbWaJR
4MRlEI2LVvfYmNCmWLIuOAsfQAnmzge++Bdns0rQ7HZKppdjCCf65NRXxnrMG6KE
zPwlSGXioA==
-----END CERTIFICATE-----
"""

_PRIVATE_KEY = """-----BEGIN PRIVATE KEY-----
MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQC1p1mfTj5QN+e+
CcbkqtsQg1F2Hdzjg/4rfhAlNBeUzebAnDOlOM6X1ySrazHQf7A4iPWcx7aErKjC
SqdjVTFWkltV5PvBSRDaLJvbspclF8275JJwcqT3k6iY48teLdVpWrvw1OH1BUgi
PKp6XQB3We5cxXdywgAgLwmUPpzDdEimvXXZoP2/AszwEFjen9MDKxq41cJ1I0Ix
CucsJwwcU5F9RLPEw3p65WMMCDiHnk6Z+LBqudJTvNI8BeoTBuowF356aaTAWLPC
OGPFHZDom9d9TpPdhSpbuAsXkwREessyZLAFfEjFokSkA9O3RAeaUYALItj5brmK
XOQIl3Y5AgMBAAECggEAMBcbzObj7EzAlzjzZLouwJJjUIfbYIgjE9SN5auCyqwv
+LHhok4Fs4ELfi4kw/Pp7S2lCFp+4WTsUHN/GPcG1spsT7MNS0BSq8+/e0wuY22H
08RRWF+heMGoksFLp6UuqHOS6dER+NHrJNU2tKpGY1J1ni3Qz5q4ecy6Fnr0+XEv
VClAJAiVzO6GtQ+pD7UV+AA0INnHR+qhTBzg7rf/Mxrkfc7XUtrH11pqZDoptYVE
NoCi2yXCH9NJ2sZu25kGAq364k2eIMh8/CClC9CWdQPwoADekie5+YGmc+XkaZ09
ajy6vObccJxQCFIMXeXlBMpR9pKLILgo3wrfI35r2QKBgQDWrhGzMYmXQfZP7FRe
4WBaSg2lZZt94jSJs+POIclMHk7SBjBUjQ6TQHK+mH+LRitlSIzg/aTuQQ/YnKo4
hwwMwnaZ3zlqvkjiPhi6dZuiTkW/+8Qt5GXgZb0FmIylL2esGmI0PG+QpEjKpzva
k7hZOX36psX33PzlB8TZ+m26owKBgQDYnfpXXlXB8oYLs7KPnV4UDFS6kdlxfiih
jG+06zCZyjobrICv0Gt4zLIublT53lbwlLia3zAjs+1uCfHB+8QSDJHohuDHcajr
kH5uMfGfq1QICydTElHHXEwswFNb0GFd9T6j5R6kP46Gsmoe+Lj+wOu825sIMX7w
Sngx+iHVcwKBgAK7YFpJbRjRrBLETSaP86vyeU37hwkJEeYWtfVvlKI1WIdvsLnC
ucbB8TPfuO/Kys6VJ8XNNIYbqdn11Gy3uz2GVAANSSvElCTc6W+mA38DQ/v3dj2n
RGygntEtaczIV6dnBoznn/vVO1/o1OgpLRVll+bSrH5E/6rieGzVtQPFAoGBAMTP
A9zNgO0uBEwtgFarjAZ7DaBVU1KiZRgiGHwwE4AnFJSALouOuZaPpHQ7GknLkQIU
nYVYssw9dcM8lcLBlGsxsH+rzB5pTih4p+ZGyllfuIF2YMxBILwZR7yBITuSIGTV
pYwTZ7ujY0NOZsys/qe3cHeurzTKg6KehoYh1QuxAoGBAMgYQ0hczGO2WEjY8x7V
1CCGRPHRPrAKMZKmxb0yH7JwU9pqov8shW54qYhid6pLui+7ZPltDp5S9nr6XnTo
0/qgyfqBbHZDNrnnbvUUwov9nld+JW84dXFV9wMKhdhiKBeVZ/358S5AuJp4l2zU
9hxxHD+HbDqclas+N86iXq0I
-----END PRIVATE KEY-----
"""


class TlsWebSocketServer:
    """Runs a loopback WSS endpoint with a test-only self-signed certificate."""

    def __init__(
        self,
        *,
        token: str,
        rejection_status: int | None = None,
        refuse_upgrade_silently: bool = False,
    ) -> None:
        self._token = token
        self._rejection_status = rejection_status
        self._refuse_upgrade_silently = refuse_upgrade_silently
        self._temporary = TemporaryDirectory()
        self._server = None
        self.authorization_values: list[str | None] = []
        self.messages: list[str] = []

    async def __aenter__(self) -> "TlsWebSocketServer":
        root = Path(self._temporary.name)
        certificate_path = root / "certificate.pem"
        private_key_path = root / "private-key.pem"
        certificate_path.write_text(_CERTIFICATE, encoding="ascii")
        private_key_path.write_text(_PRIVATE_KEY, encoding="ascii")
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(certificate_path, private_key_path)

        async def process_request(connection, request):
            self.authorization_values.append(request.headers.get("Authorization"))
            if self._rejection_status is not None:
                return connection.respond(
                    HTTPStatus(self._rejection_status),
                    "raw-response-body-must-remain-private",
                )
            if self._refuse_upgrade_silently:
                return connection.respond(
                    HTTPStatus.OK,
                    "raw-response-body-must-remain-private",
                )
            if request.headers.get("Authorization") != f"Bearer {self._token}":
                return connection.respond(HTTPStatus.UNAUTHORIZED, "denied")
            return None

        async def handler(connection) -> None:
            try:
                while True:
                    self.messages.append(await connection.recv())
            except ConnectionClosed:
                return

        # A dual-stack "localhost" bind takes two different ephemeral ports, so the
        # published one may not be the one the client resolves to.
        self._server = await serve(
            handler,
            "127.0.0.1",
            0,
            ssl=server_context,
            process_request=process_request,
        )
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        self._temporary.cleanup()

    @property
    def base_url(self) -> str:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("server is not running")
        port = self._server.sockets[0].getsockname()[1]
        return f"https://127.0.0.1:{port}"

    def trusted_client_context(self) -> ssl.SSLContext:
        return ssl.create_default_context(cadata=_CERTIFICATE)


async def let_server_observe_close() -> None:
    """Yield briefly so the test server observes the client's immediate close."""
    for _ in range(3):
        await asyncio.sleep(0)
