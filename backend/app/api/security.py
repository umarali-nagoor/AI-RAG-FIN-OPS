"""API security: API-key authentication (constant-time), per-client rate limiting, security headers, body-size limit."""

from __future__ import annotations

import hmac
import time
import uuid
from collections import defaultdict, deque
from threading import Lock

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from app.core.config import Settings
from app.core.logging import request_id_var

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cache-Control": "no-store",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'",
}


def make_auth_dependency(settings: Settings):
    keys = [k.encode() for k in settings.api_key_list]

    def require_api_key(key: str | None = Depends(_api_key_header)) -> None:
        if not keys:  # development without keys configured; production refuses to start in that state
            return
        supplied = (key or "").encode()
        ok = False
        for k in keys:  # compare against every key to avoid early-exit timing differences
            ok |= hmac.compare_digest(supplied, k)
        if not ok:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized", headers={"WWW-Authenticate": "ApiKey"})

    return require_api_key


class RateLimiter:
    """Sliding-window limiter keyed by API key (or client IP). In-process; use a shared store when scaling out."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, ident: str) -> bool:
        if self.per_minute <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            q = self._hits[ident]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return False
            q.append(now)
            if len(self._hits) > 10_000:  # bound memory
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] > 60]:
                    del self._hits[k]
            return True


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings, limiter: RateLimiter):  # noqa: ANN001
        super().__init__(app)
        self.max_body = settings.max_diff_bytes + 64_000
        self.limiter = limiter

    @staticmethod
    def _exempt(path: str) -> bool:
        return path in ("/healthz", "/readyz") or path.startswith("/assets") or path == "/"

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        rid = request.headers.get("X-Request-ID", "")
        rid = rid if rid.isalnum() and len(rid) <= 64 else uuid.uuid4().hex
        token = request_id_var.set(rid)
        try:
            length = request.headers.get("content-length")
            if length and length.isdigit() and int(length) > self.max_body:
                resp: Response = JSONResponse({"error": "Request too large"}, status_code=413)
            else:
                ident = request.headers.get("X-API-Key") or (request.client.host if request.client else "anon")
                if not self._exempt(request.url.path) and not self.limiter.allow(ident):
                    resp = JSONResponse({"error": "Too many requests"}, status_code=429, headers={"Retry-After": "60"})
                else:
                    resp = await call_next(request)
            resp.headers["X-Request-ID"] = rid
            for k, v in SECURITY_HEADERS.items():
                resp.headers.setdefault(k, v)
            return resp
        finally:
            request_id_var.reset(token)
