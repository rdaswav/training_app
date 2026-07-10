"""HTTP Basic Auth gate for the whole app, enabled only when AUTH_USERNAME
and AUTH_PASSWORD are both set (see config.py)."""
from __future__ import annotations

import base64
import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app import config


class BasicAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not (config.AUTH_USERNAME and config.AUTH_PASSWORD):
            return await call_next(request)

        if _credentials_valid(request.headers.get("authorization", "")):
            return await call_next(request)

        return Response(
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Training App"'},
        )


def _credentials_valid(authorization_header: str) -> bool:
    if not authorization_header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(authorization_header[len("Basic "):]).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    username, _, password = decoded.partition(":")
    return hmac.compare_digest(username, config.AUTH_USERNAME) and hmac.compare_digest(
        password, config.AUTH_PASSWORD
    )
