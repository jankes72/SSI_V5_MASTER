
API_VERSION = "v1"

ROUTES = {
    "health": "/api/v1/health",
    "session": "/api/v1/session",
    "devices": "/api/v1/devices",
    "commands": "/api/v1/commands",
    "command_status": "/api/v1/commands/{command_id}",
    "clarifications": "/api/v1/clarifications",
    "approvals": "/api/v1/approvals",
    "audit": "/api/v1/audit",
    "emergency": "/api/v1/emergency-stop",
}

FORBIDDEN_ROUTES = {
    "/runtime",
    "/ollama",
    "/terminal",
    "/files",
}

def validate_route(route: str):
    if route in FORBIDDEN_ROUTES:
        raise ValueError("FORBIDDEN_ROUTE")

    if not route.startswith("/api/v1/"):
        raise ValueError("UNVERSIONED_ROUTE")

    return True
