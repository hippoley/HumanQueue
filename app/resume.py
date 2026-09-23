from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx

from .models import AttentionRequest


def signed_payload(req: AttentionRequest, resolution: dict[str, Any]) -> tuple[bytes, str | None]:
    body = json.dumps(
        {
            "event": "attention.resolved",
            "request_id": req.id,
            "source": req.source,
            "source_ref": req.source_ref,
            "resolution": resolution,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    if not req.resume.secret:
        return body, None
    sig = hmac.new(req.resume.secret.encode(), body, hashlib.sha256).hexdigest()
    return body, f"sha256={sig}"


async def resume(req: AttentionRequest, resolution: dict[str, Any]) -> dict[str, Any]:
    if req.resume.mode != "webhook" or not req.resume.url:
        return {"delivered": False, "reason": "no_resume_target"}
    body, signature = signed_payload(req, resolution)
    headers = {"content-type": "application/json", "x-attention-request-id": req.id}
    if signature:
        headers["x-attention-signature"] = signature
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(str(req.resume.url), content=body, headers=headers)
    return {"delivered": response.is_success, "status_code": response.status_code}
