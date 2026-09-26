from __future__ import annotations

import hmac

from fastapi import HTTPException, Request

from humanqueue.config import gateway_token


def extract_bearer(request: Request) -> str | None:
    value = request.headers.get("authorization", "")
    if not value.lower().startswith("bearer "):
        return None
    return value.split(" ", 1)[1].strip()


def require_gateway_token(request: Request) -> None:
    expected = gateway_token()
    if not expected:
        return
    supplied = extract_bearer(request)
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=401,
            detail="human:// gateway token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
