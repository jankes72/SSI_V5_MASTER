
from dataclasses import dataclass

class LocalTransportError(Exception):
    pass

@dataclass(frozen=True)
class LocalTransportPolicy:
    bind_host: str = "127.0.0.1"
    allow_debug_routes: bool = False
    require_authentication: bool = True
    require_origin_validation: bool = True
    allow_runtime_routes: bool = False
    allow_ollama_routes: bool = False
    allow_filesystem_routes: bool = False
    allow_terminal_routes: bool = False

    def validate_origin(self, origin: str):
        allowed = {
            "http://127.0.0.1",
            "http://localhost",
            "https://127.0.0.1",
            "https://localhost",
        }
        if self.require_origin_validation and origin not in allowed:
            raise LocalTransportError("ORIGIN_REJECTED")
        return True

    def authorize_route(self, route: str, authenticated: bool):
        forbidden_prefixes = (
            "/runtime",
            "/ollama",
            "/files",
            "/terminal",
            "/debug",
        )

        if route.startswith(forbidden_prefixes):
            raise LocalTransportError("FORBIDDEN_ROUTE")

        if self.require_authentication and not authenticated:
            raise LocalTransportError("AUTHENTICATION_REQUIRED")

        return True
