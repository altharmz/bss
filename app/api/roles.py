"""Role gates that API modules import. Not in app/auth.py: app.auth imports app.api, whose
package imports every router, so a router importing app.auth would be a circular import when
app.auth loads first (the worker does)."""

from fastapi import Request

from app.api.errors import TmfError


def require_admin_or_service(request: Request) -> None:
    """The System screens' endpoints (#467, docs/decisions/users.md U4): an admin session, or a
    service principal - the MCP connector, whose `mcp` users an administrator creates, and the
    read-only smoke credential. A `user` session gets 403. Runs after app.auth.authenticate,
    which sets request.state.role."""
    if getattr(request.state, "role", None) not in {"admin", "service"}:
        raise TmfError(403, "administrator role required")
