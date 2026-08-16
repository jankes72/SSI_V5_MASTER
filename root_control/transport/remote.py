
from dataclasses import dataclass
from urllib.parse import urlparse

class RemoteTransportError(Exception):
    pass

@dataclass(frozen=True)
class RemoteChannelPolicy:
    tls_required: bool = True
    authentication_required: bool = True
    device_trust_required: bool = True
    allow_public_runtime_port: bool = False
    allow_direct_runtime: bool = False
    allow_direct_ollama: bool = False
    allow_direct_files: bool = False
    allow_direct_terminal: bool = False

    def validate_endpoint(self, url: str):
        u = urlparse(url)

        if self.tls_required and u.scheme != "https":
            raise RemoteTransportError("TLS_REQUIRED")

        if not u.hostname:
            raise RemoteTransportError("INVALID_ENDPOINT")

        return True

    def authorize(
        self,
        *,
        authenticated: bool,
        trusted_device: bool,
        target: str
    ):
        if not authenticated:
            raise RemoteTransportError("AUTHENTICATION_REQUIRED")

        if not trusted_device:
            raise RemoteTransportError("TRUSTED_DEVICE_REQUIRED")

        forbidden = {
            "runtime",
            "ollama",
            "filesystem",
            "terminal",
        }

        if target in forbidden:
            raise RemoteTransportError("FORBIDDEN_TARGET")

        return True
