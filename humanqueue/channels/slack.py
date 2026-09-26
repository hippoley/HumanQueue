from __future__ import annotations

from threading import Event
from typing import Any

import httpx

from app.models import AttentionRequest
from app.protocol import default_options
from humanqueue.config import channel_configs, gateway_token, gateway_url
from .webhook import _bounded_context

ACTION_ID = "humanq_decision"


def _decision_value(request_id: str, action: str) -> str:
    return f"{request_id}|{action}"


def parse_decision_value(value: str) -> tuple[str, str] | None:
    parts = value.split("|", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


def _resolve_local(request_id: str, action: str, actor: str) -> tuple[bool, str]:
    token = gateway_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        response = httpx.post(
            gateway_url() + f"/v1/requests/{request_id}/resolve",
            headers=headers,
            json={"actor": actor, "action": action, "values": {}},
            timeout=4,
        )
    except Exception as exc:
        return False, str(exc)
    if response.status_code == 200:
        return True, "Resolved in human://"
    if response.status_code == 409:
        return False, "Already resolved"
    return False, f"Gateway returned HTTP {response.status_code}"


def render_blocks(request: AttentionRequest) -> list[dict[str, Any]]:
    ctx = _bounded_context(request.context)
    session = ctx.get("session_context") if isinstance(ctx, dict) else None
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": f"human:// - {request.source}"[:150]}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{request.title}*\n{request.summary[:900]}"}},
    ]
    if isinstance(session, dict):
        lines = []
        if session.get("latest_user_prompt"):
            lines.append(f"*User:* {str(session['latest_user_prompt'])[:700]}")
        if session.get("latest_assistant_message"):
            lines.append(f"*Agent:* {str(session['latest_assistant_message'])[:700]}")
        if lines:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}})
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": (
            f"priority *{request.priority}* - risk *{request.signals.risk_if_wrong:.2f}* - "
            f"unblocks *{request.signals.downstream_blocked}* - request {request.id}"
        )}],
    })
    elements = []
    options = request.options or default_options(request.kind)
    for option in options[:5]:
        element: dict[str, Any] = {
            "type": "button",
            "text": {"type": "plain_text", "text": str(option.label)[:75]},
            "action_id": ACTION_ID,
            "value": _decision_value(request.id, str(option.id)),
        }
        if option.style == "danger":
            element["style"] = "danger"
        elif option.style == "safe":
            element["style"] = "primary"
        elements.append(element)
    if elements:
        blocks.append({"type": "actions", "elements": elements})
    return blocks


def publish_one(name: str, cfg: dict[str, Any], request: AttentionRequest) -> dict[str, Any]:
    try:
        from slack_sdk.web import WebClient
    except ImportError:
        return {"channel": name, "delivered": False, "error": "slack-sdk is not installed"}
    bot_token = str(cfg.get("bot_token") or "")
    channel_id = str(cfg.get("channel_id") or "")
    if not bot_token or not channel_id:
        return {"channel": name, "delivered": False, "error": "slack config incomplete"}
    try:
        result = WebClient(token=bot_token).chat_postMessage(
            channel=channel_id,
            text=f"human://: {request.title}",
            blocks=render_blocks(request),
        )
        return {"channel": name, "delivered": bool(result.get("ok", True)), "ts": result.get("ts")}
    except Exception as exc:
        return {"channel": name, "delivered": False, "error": str(exc)}


def run_socket_mode(name: str) -> int:
    cfg = channel_configs().get(name)
    if not cfg or cfg.get("type") != "slack":
        raise RuntimeError(f"Slack channel not found: {name}")
    app_token = str(cfg.get("app_token") or "")
    bot_token = str(cfg.get("bot_token") or "")
    channel_id = str(cfg.get("channel_id") or "")
    if not app_token or not bot_token or not channel_id:
        raise RuntimeError("Slack channel config is incomplete")
    try:
        from slack_sdk.socket_mode import SocketModeClient
        from slack_sdk.socket_mode.response import SocketModeResponse
        from slack_sdk.web import WebClient
    except ImportError as exc:
        raise RuntimeError("slack-sdk is required for Slack Socket Mode") from exc

    web = WebClient(token=bot_token)
    client = SocketModeClient(app_token=app_token, web_client=web)

    def process(client: Any, req: Any) -> None:
        if req.type != "interactive":
            return
        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        payload = req.payload or {}
        if payload.get("type") != "block_actions":
            return
        container = payload.get("container") or {}
        if channel_id and str(container.get("channel_id") or "") != channel_id:
            return
        actions = payload.get("actions") or []
        picked = next((a for a in actions if a.get("action_id") == ACTION_ID), None)
        if not picked:
            return
        parsed = parse_decision_value(str(picked.get("value") or ""))
        if not parsed:
            return
        rid, decision = parsed
        actor = f"slack:{(payload.get('user') or {}).get('id') or 'human'}"
        ok, message = _resolve_local(rid, decision, actor)
        if ok:
            try:
                blocks = [b for b in (payload.get("message", {}).get("blocks") or []) if b.get("type") != "actions"]
                blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"{message} by {actor}"}]})
                web.chat_update(
                    channel=channel_id,
                    ts=(payload.get("message") or {}).get("ts"),
                    text=message,
                    blocks=blocks,
                )
            except Exception:
                pass

    client.socket_mode_request_listeners.append(process)
    print(f"human:// Slack channel '{name}' listening via Socket Mode")
    print("Press Ctrl+C to stop.")
    client.connect()
    try:
        Event().wait()
    except KeyboardInterrupt:
        client.close()
        return 0
