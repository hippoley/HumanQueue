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


def _receipt_confirmation(request_id: str, response: httpx.Response) -> tuple[bool, dict[str, Any] | None]:
    """Confirm semantic resume only when the target explicitly binds its receipt to this request."""
    try:
        payload = response.json()
    except Exception:
        return False, None
    if not isinstance(payload, dict):
        return False, None

    receipt = {
        "request_id": payload.get("request_id"),
        "resumed": payload.get("resumed"),
    }
    confirmed = receipt["request_id"] == request_id and receipt["resumed"] is True
    return confirmed, receipt


async def resume(req: AttentionRequest, resolution: dict[str, Any]) -> dict[str, Any]:
    if req.resume.mode != "webhook" or not req.resume.url:
        return {"delivered": False, "confirmed": False, "reason": "no_resume_target"}

    body, signature = signed_payload(req, resolution)
    headers = {"content-type": "application/json", "x-attention-request-id": req.id}
    if signature:
        headers["x-attention-signature"] = signature

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(str(req.resume.url), content=body, headers=headers)
    except Exception as exc:
        return {
            "delivered": False,
            "confirmed": False,
            "reason": "resume_transport_error",
            "error": str(exc),
        }

    confirmed, receipt = _receipt_confirmation(req.id, response)
    result: dict[str, Any] = {
        "delivered": response.is_success,
        "confirmed": bool(response.is_success and confirmed),
        "status_code": response.status_code,
    }
    if receipt is not None:
        result["receipt"] = receipt
    if response.is_success and not result["confirmed"]:
        result["reason"] = "transport_delivered_resume_unconfirmed"
    return result
