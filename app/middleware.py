"""NV Tools auth middleware + CSP header for iframe embedding.

Contract (see nv-tools-service-deploy skill):
- ``/healthz`` is open (no auth).
- Every other path requires ``Authorization: Bearer <NV_TOKEN>``; the X-User-*
  headers populate ``request.state``. DEV_MODE injects fakes when no
  Authorization header is present so the app runs locally with no proxy.
- Emit ``Content-Security-Policy: frame-ancestors ...`` on every response;
  never ``X-Frame-Options``.

Adapted from router/app/middleware.py (dropped the /api/v1 service-token path —
this app has no machine-to-machine callers yet).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings

_CSP = "frame-ancestors https://tools.novacancies.space https://novacancies.space"


class NVToolsAuthMiddleware(BaseHTTPMiddleware):
    """Validate the inbound bearer and attach caller identity to request.state."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        settings = get_settings()
        prefix = settings.url_prefix
        path = request.url.path

        # Read from the raw ASGI scope so DEV_MODE can inject headers before
        # Starlette caches its Headers view.
        scope_headers: list[tuple[bytes, bytes]] = list(request.scope.get("headers", []))
        headers_lookup = {
            name.decode("latin-1").lower(): value.decode("latin-1")
            for name, value in scope_headers
        }

        if path == f"{prefix}/healthz":
            response = await call_next(request)
            response.headers["content-security-policy"] = _CSP
            return response

        if settings.dev_mode and "authorization" not in headers_lookup:
            scope_headers = _inject_dev_headers(
                scope_headers,
                token=settings.nv_token,
                rank=settings.dev_user_rank,
                teams=settings.dev_user_teams,
            )
            request.scope["headers"] = scope_headers
            headers_lookup = {
                name.decode("latin-1").lower(): value.decode("latin-1")
                for name, value in scope_headers
            }

        if headers_lookup.get("authorization") != f"Bearer {settings.nv_token}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        request.state.user_name = headers_lookup.get("x-user-name", "")
        request.state.user_rank = headers_lookup.get("x-user-rank", "")
        request.state.user_teams = [
            t.strip() for t in headers_lookup.get("x-user-teams", "").split(",") if t.strip()
        ]
        request.state.user_main_character_id = headers_lookup.get("x-user-main-character-id", "")

        response = await call_next(request)
        response.headers["content-security-policy"] = _CSP
        return response


def _inject_dev_headers(
    headers: list[tuple[bytes, bytes]],
    token: str,
    rank: str = "",
    teams: str = "",
) -> list[tuple[bytes, bytes]]:
    """Add fake auth + user headers to the ASGI scope (DEV_MODE only)."""
    effective_rank = rank.strip() or "Member"
    effective_teams = teams.strip() or ""
    extra = [
        (b"authorization", f"Bearer {token}".encode()),
        (b"x-user-name", b"Test User"),
        (b"x-user-rank", effective_rank.encode("latin-1")),
        (b"x-user-teams", effective_teams.encode("latin-1")),
        (b"x-user-main-character-id", b"0"),
    ]
    existing_names = {name for name, _ in headers}
    return [*headers, *[(n, v) for n, v in extra if n not in existing_names]]
