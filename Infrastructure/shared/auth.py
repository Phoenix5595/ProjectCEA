"""API-key authentication + WebSocket origin check.

Two public entry points:

* :func:`install_api_key_middleware` — ASGI middleware that short-circuits
  any HTTP request matching ``/api/*`` with 401 when the ``X-API-Key``
  header does not match ``$CEA_API_KEY``. Gated by
  ``CEA_API_KEY_REQUIRE=true`` so deploys are reversible by env flip.

* :func:`check_websocket_auth` — call from WebSocket handlers; closes the
  socket with code 1008 on missing/bad token or disallowed origin.

Both honor the same gate flag and the same origin list. Both use
:func:`hmac.compare_digest` for constant-time string comparison.
"""

from __future__ import annotations

import hmac
import os

from fastapi import WebSocket
from starlette.types import ASGIApp, Receive, Scope, Send

from shared.infra_logging import get_logger
from shared.middleware import parse_origins_env

logger = get_logger(__name__)


# Paths exempt from API-key enforcement. These are intentionally open:
#   - /health, /ready   : orchestrator probes (never carry secrets)
#   - /ws, /ws/*        : handled by check_websocket_auth instead
#   - /                 : root (SPA index or welcome JSON)
#   - /docs, /redoc,
#     /openapi.json     : gated separately via docs_kwargs()
#   - /logo.png,
#     /favicon.*,
#     /assets/*         : static content served from SPA bundle
# All other paths (prefix /api/) MUST carry the key when enforcement is
# on.
_OPEN_PREFIXES: tuple[str, ...] = (
    "/health",
    "/ready",
    "/ws",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/logo.png",
    "/favicon",
    "/assets",
)


def auth_required() -> bool:
    """True iff ``CEA_API_KEY_REQUIRE=true`` in the process env.

    Default is False. Setting the flag is the single operator lever to
    turn enforcement on; unsetting it turns enforcement off without a
    code redeploy.
    """
    return os.environ.get("CEA_API_KEY_REQUIRE", "").strip().lower() == "true"


def _expected_key() -> str | None:
    """Return CEA_API_KEY or None (never empty string)."""
    k = os.environ.get("CEA_API_KEY", "").strip()
    return k or None


def _is_protected_path(path: str) -> bool:
    """True if ``path`` must carry an API key when enforcement is on.

    Protection surface is ``/api/*``. A route like ``/health`` or
    ``/logo.png`` is always open. Static assets and SPA fallback routes
    (``/`` and any non-api path) stay open because the SPA needs to
    fetch them before it is logged in.
    """
    if not path.startswith("/api/") and path != "/api":
        return False
    # Path starts with /api. Check against the open list anyway (belt +
    # braces). Nothing today starts with both ``/api`` and one of the
    # open prefixes, but keep the guard so future routes cannot silently
    # slip past.
    return not any(path.startswith(p) for p in _OPEN_PREFIXES)


class APIKeyAuthMiddleware:
    """ASGI middleware enforcing ``X-API-Key`` on ``/api/*`` HTTP paths.

    Attach via ``app.add_middleware(APIKeyAuthMiddleware)``. Evaluation
    order: installed after :func:`shared.middleware.setup_cors` so that
    CORS wraps this layer and runs its preflight handling first. Inside
    this middleware we also short-circuit ``OPTIONS`` just in case
    middleware ordering is later flipped.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # Let WS and lifespan through untouched. WS auth is enforced
            # inside each handler via check_websocket_auth.
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        method: str = scope.get("method", "GET")

        # CORS preflight never carries custom headers; pass through so
        # the CORSMiddleware can answer.
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        if not auth_required():
            await self.app(scope, receive, send)
            return

        if not _is_protected_path(path):
            await self.app(scope, receive, send)
            return

        expected = _expected_key()
        if expected is None:
            # Enforcement requested but no key configured. Fail CLOSED
            # with 503 so a misconfigured deploy is loud, not silently
            # permissive.
            logger.error(
                "API-key auth requested (CEA_API_KEY_REQUIRE=true) but CEA_API_KEY is unset; refusing request"
            )
            await _send_plain(send, 503, b"auth misconfigured")
            return

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])
        }
        provided = headers.get("x-api-key", "")

        if not provided or not hmac.compare_digest(provided, expected):
            logger.warning("API-key auth rejected: path=%s method=%s", path, method)
            await _send_plain(send, 401, b"invalid api key")
            return

        await self.app(scope, receive, send)


async def _send_plain(send: Send, status: int, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"text/plain; charset=utf-8")],
        }
    )
    await send({"type": "http.response.body", "body": body})


# =====================================================================
# WebSocket auth (Phase 3.2)
# =====================================================================
#
# Browsers cannot set arbitrary headers on WebSocket handshakes, so we
# accept the key via a query param (``?token=...``) OR require the
# Origin header to be in the FRONTEND_ORIGINS allow-list. Either path
# is sufficient; both give defense-in-depth when Caddy adds origin
# checks in Phase 3.4b.
#
# Use by calling ``await check_websocket_auth(ws)`` BEFORE
# ``ws.accept()``. On failure the socket is closed with code 1008
# (policy violation) and the coroutine returns False.


async def check_websocket_auth(ws: WebSocket) -> bool:
    """Validate a WebSocket connection; close with 1008 on failure.

    Returns True if the connection is allowed to proceed (handler should
    ``await ws.accept()``), False otherwise (handler must return
    immediately — the socket is already closed).
    """
    if not auth_required():
        return True

    # Token path.
    expected = _expected_key()
    if expected:
        token = ws.query_params.get("token", "")
        if token and hmac.compare_digest(token, expected):
            return True

    # Origin path.
    origins = parse_origins_env()
    if origins:
        origin = ws.headers.get("origin", "")
        if origin and origin in origins:
            return True

    # Neither token nor origin matched.
    try:
        await ws.close(code=1008, reason="auth")
    except Exception:
        # Connection may already be gone; swallow and move on.
        pass
    logger.warning(
        "WebSocket auth rejected: path=%s origin=%s",
        ws.scope.get("path", ""),
        ws.headers.get("origin", "<none>"),
    )
    return False


# =====================================================================
# Max-connection guard for WebSockets (Phase 3.2)
# =====================================================================
#
# Cheap per-process counter; each WebSocket handler increments on accept
# and decrements on close. Simpler than a shared Redis counter because
# each service has a single process today (uvicorn --workers 1).


class WebSocketConnectionLimiter:
    """In-process WebSocket connection cap.

    Usage:

        limiter = WebSocketConnectionLimiter()
        ...
        if not await limiter.acquire(ws):
            return  # 1013 (try again later) already sent
        try:
            await ws.accept()
            ...
        finally:
            limiter.release()
    """

    def __init__(self, *, env_var: str = "CEA_WS_MAX_CONNECTIONS", default: int = 50):
        try:
            self.limit = int(os.environ.get(env_var, str(default)))
        except ValueError:
            self.limit = default
        self.active = 0

    async def acquire(self, ws: WebSocket) -> bool:
        if self.active >= self.limit:
            try:
                await ws.close(code=1013, reason="too many connections")
            except Exception:
                pass
            logger.warning(
                "WebSocket cap hit: active=%d limit=%d path=%s",
                self.active,
                self.limit,
                ws.scope.get("path", ""),
            )
            return False
        self.active += 1
        return True

    def release(self) -> None:
        if self.active > 0:
            self.active -= 1


__all__ = [
    "APIKeyAuthMiddleware",
    "auth_required",
    "check_websocket_auth",
    "WebSocketConnectionLimiter",
]
