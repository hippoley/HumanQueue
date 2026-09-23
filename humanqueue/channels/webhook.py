from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx

from app.models import AttentionRequest
from app.protocol import uri_for_kind
from humanqueue.config import channel_configs, gateway_url


def _bounded_context(context: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    why = context.get("why_now")
    if why:
        out["why_now"] = str(why)[:500]

    handle = context.get("native_handle")
    if isinstance(handle, dict):
        out["native_handle"] = {
            k: handle.get(k)
            for k in ("provider", "session_id", "turn_id", "tool_use_id", "resume_kind")
            if handle.get(k) is not None
        }

    tool_name = context.get("tool_name")
    if tool_name:
        out["tool_name"] = str(tool_name)[:120]

    tool_input = context.get("tool_input")
    if isinstance(tool_input, dict):
        if "command" in tool_input:
            out["tool_input"] = {"command": str(tool_input["command"])[:1000]}
    return out


def _sign(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def publish_request(request: AttentionRequest) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    configs = channel_configs()

    for name, cfg in configs.items():
        if cfg.get("type") != "webhook" or not cfg.get("enabled", True):
            continue
        url = cfg.get("url")
        secret = cfg.get("secret")
        if not url or not secret:
            continue

        payload = {
            "event": "human.request.created",
            "channel": name,
            "request": {
                "id": request.id,
                "uri": uri_for_kind(request.kind),
                "source": request.source,
                "source_ref": request.source_ref,
                "title": request.title,
                "summary": request.summary,
                "priority": request.priority,
                "surface_mode": request.surface_mode.value,
                "signals": request.signals.model_dump(mode="json"),
                "actions": [o.model_dump(mode="json") for o in request.options],
                "context": _bounded_context(request.context),
            },
            "resolve": {
                "url": f"{gateway_url()}/channels/{name}/resolve/{request.id}",
                "method": "POST",
                "signature_header": "X-Human-Channel-Signature",
            },
        }

        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
        headers = {
            "content-type": "application/json",
            "X-Human-Channel": name,
            "X-Human-Channel-Signature": _sign(secret, body),
        }
        try:
            response = httpx.post(str(url), content=body, headers=headers, timeout=1.2)
            results.append({
                "channel": name,
                "delivered": response.is_success,
                "status_code": response.status_code,
            })
        except Exception as exc:
            results.append({"channel": name, "delivered": False, "error": str(exc)})
    return results


def verify_resolution(name: str, body: bytes, signature: str | None) -> bool:
    cfg = channel_configs().get(name) or {}
    secret = cfg.get("secret")
    if cfg.get("type") != "webhook" or not secret or not signature:
        return False
    expected = _sign(secret, body)
    return hmac.compare_digest(signature, expected)
