"""Structured, TLS-safe Backend endpoint construction."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from urllib.parse import quote, unquote, urlsplit, urlunsplit


class EndpointProfile(str, Enum):
    """Endpoint scheme policy; development is intentionally opt-in."""

    PRODUCTION = "production"
    DEVELOPMENT = "development"


@dataclass(frozen=True)
class BackendEndpointConfig:
    """The parsed Backend base URL and explicit Worker endpoint templates."""

    public_base_url: str
    worker_websocket_path: str
    worker_status_path_template: str
    websocket_scheme_policy: str
    status_authentication_mode: str
    tls_policy: str
    _status_scheme: str
    _netloc: str

    @classmethod
    def from_base_url(
        cls,
        value: str,
        profile: EndpointProfile = EndpointProfile.PRODUCTION,
    ) -> "BackendEndpointConfig":
        """Parse a Backend base URL under the selected explicit profile."""
        if not isinstance(profile, EndpointProfile):
            raise ValueError("profile must be an EndpointProfile")
        if (
            not isinstance(value, str)
            or not value.strip()
            or value != value.strip()
            or _contains_disallowed_raw_url_characters(value)
        ):
            raise ValueError("Backend base URL must be a non-empty URL without whitespace")

        parsed = urlsplit(value)
        if parsed.scheme not in _allowed_schemes(profile):
            raise ValueError("Backend base URL scheme is not allowed for this profile")
        if not parsed.netloc or parsed.hostname is None:
            raise ValueError("Backend base URL must include a host")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Backend base URL must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("Backend base URL must not contain a query or fragment")
        try:
            parsed.port
        except ValueError as error:
            raise ValueError("Backend base URL has an invalid port") from error

        base_path = _normalise_base_path(parsed.path)
        status_scheme = parsed.scheme
        websocket_scheme = "wss" if status_scheme == "https" else "ws"
        netloc = parsed.netloc
        public_base_url = urlunsplit((status_scheme, netloc, base_path, "", ""))
        return cls(
            public_base_url=public_base_url,
            worker_websocket_path=f"{base_path}/ws/workers",
            worker_status_path_template=f"{base_path}/api/workers/{{worker_id}}",
            websocket_scheme_policy=websocket_scheme,
            status_authentication_mode="NONE",
            tls_policy="REQUIRED" if status_scheme == "https" else "DEVELOPMENT_ONLY",
            _status_scheme=status_scheme,
            _netloc=netloc,
        )

    @classmethod
    def from_development_base_url(cls, value: str) -> "BackendEndpointConfig":
        """Construct a local-development endpoint; test code must opt in explicitly."""
        return cls.from_base_url(value, EndpointProfile.DEVELOPMENT)

    @property
    def worker_websocket_url(self) -> str:
        return urlunsplit(
            (self.websocket_scheme_policy, self._netloc, self.worker_websocket_path, "", "")
        )

    def worker_status_url(self, worker_id: str) -> str:
        if not isinstance(worker_id, str) or not worker_id:
            raise ValueError("worker_id must be a non-empty string")
        path = self.worker_status_path_template.format(worker_id=quote(worker_id, safe=""))
        return urlunsplit((self._status_scheme, self._netloc, path, "", ""))


def _allowed_schemes(profile: EndpointProfile) -> tuple[str, ...]:
    return ("https",) if profile is EndpointProfile.PRODUCTION else ("http", "https")


def _contains_disallowed_raw_url_characters(value: str) -> bool:
    return any(character.isspace() or ord(character) < 0x20 for character in value)


def _normalise_base_path(path: str) -> str:
    decoded_path = unquote(path)
    if "\\" in decoded_path:
        raise ValueError("Backend base URL path must not contain backslashes")
    components = [component for component in decoded_path.split("/") if component]
    if any(component in {".", ".."} for component in components):
        raise ValueError("Backend base URL path must not contain traversal")
    encoded_components = [
        quote(component, safe="!$&'()*+,;=:@-._~") for component in components
    ]
    return "/" + "/".join(encoded_components) if encoded_components else ""
