from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.models import AttentionRequest
from app.protocol import uri_for_kind
from humanqueue.config import channel_configs, gateway_token, gateway_url
from .webhook import _bounded_context


def _api(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def _callback_data(request_id: str, action: str) -> str:
    # Human Queue request IDs and normal action IDs keep this under Telegram's
    # callback-data limit. Reject pathological connector-defined action IDs.
    data = f"hq|{request_id}|{action}"
    if len(data.encode("utf-8")) > 64:
        raise ValueError("Telegram callback payload exceeds 64 bytes")
    return data


def parse_callback_data(data: str) -> tuple[str, str] | None:
    parts = data.split("|", 2)
    if len(parts) != 3 or parts[0] != "hq":
        return None
    rid, action = parts[1], parts[2]
    if not rid or not action:
        return None
    return rid, action


def render_message(request: AttentionRequest) -> tuple[str, dict[str, Any]]:
    ctx = _bounded_context(request.context)
    session = ctx.get("session_context") if isinstance(ctx, dict) else None

    lines = [
        f"human:// · {request.source}",
        "",
        request.title,
    ]
    if request.summary:
        lines += ["", request.summary[:800]]

    if isinstance(session, dict):
        user = session.get("latest_user_prompt")
        agent = session.get("latest_assistant_message")
        if user:
            lines += ["", f"User: {str(user)[:500]}"]
        if agent:
            lines += ["", f"Agent: {str(agent)[:500]}"]

    lines += [
        "",
        f"Priority: {request.priority}",
        f"Risk: {request.signals.risk_if_wrong:.2f}",
        f"Unblocks: {request.signals.downstream_blocked}",
        f"Request: {request.id}",
    ]

    options = request.options or []
    if not options:
        options = [
            type("_Option", (), {"id": "approve", "label": "Approve"})(),
            type("_Option", (), {"id": "reject", "label": "Reject"})(),
        ]

    keyboard = []
    row = []
    for option in options[:6]:
        try:
            data = _callback_data(request.id, str(option.id))
        except ValueError:
            continue
        row.append({
            "text": str(option.label)[:40],
            "callback_data": data,
        })
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    return "\n".join(lines), {"inline_keyboard": keyboard}


def publish_one(name: str, cfg: dict[str, Any], request: AttentionRequest) -> dict[str, Any]:
    token = str(cfg.get("bot_token") or "")
    chat_id = cfg.get("chat_id")
    if not token or chat_id is None:
        return {"channel": name, "delivered": False, "error": "telegram config incomplete"}

    text, reply_markup = render_message(request)
    try:
        response = httpx.post(
            _api(token, "sendMessage"),
            json={
                "chat_id": chat_id,
                "text": text,
                "reply_markup": reply_markup,
                "disable_web_page_preview": True,
            },
            timeout=4,
        )
        data = response.json() if response.content else {}
        return {
            "channel": name,
            "delivered": response.is_success and bool(data.get("ok", True)),
            "status_code": response.status_code,
            "message_id": (data.get("result") or {}).get("message_id"),
        }
    except Exception as exc:
        return {"channel": name, "delivered": False, "error": str(exc)}


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
        return True, "Resolved in Human Queue"
    if response.status_code == 409:
        return False, "Already resolved"
    return False, f"Gateway returned HTTP {response.status_code}"


def handle_callback(name: str, cfg: dict[str, Any], query: dict[str, Any]) -> tuple[bool, str]:
    token = str(cfg.get("bot_token") or "")
    parsed = parse_callback_data(str(query.get("data") or ""))
    callback_id = str(query.get("id") or "")
    message = query.get("message") or {}
    chat = message.get("chat") or {}
    configured_chat = str(cfg.get("chat_id"))
    actual_chat = str(chat.get("id")) if chat.get("id") is not None else ""

    if not parsed:
        result = (False, "Unknown Human Queue action")
    elif configured_chat and actual_chat != configured_chat:
        result = (False, "This chat is not authorized for this Human Queue")
    else:
        rid, action = parsed
        actor_id = ((query.get("from") or {}).get("id"))
        result = _resolve_local(rid, action, f"telegram:{actor_id or 'human'}")

    if callback_id and token:
        try:
            httpx.post(
                _api(token, "answerCallbackQuery"),
                json={
                    "callback_query_id": callback_id,
                    "text": result[1][:180],
                    "show_alert": not result[0],
                },
                timeout=3,
            )
        except Exception:
            pass

    if result[0] and token and message.get("message_id") is not None and actual_chat:
        try:
            httpx.post(
                _api(token, "editMessageReplyMarkup"),
                json={
                    "chat_id": actual_chat,
                    "message_id": message.get("message_id"),
                    "reply_markup": {"inline_keyboard": []},
                },
                timeout=3,
            )
        except Exception:
            pass
    return result


def run_long_poll(name: str) -> int:
    cfg = channel_configs().get(name)
    if not cfg or cfg.get("type") != "telegram":
        raise RuntimeError(f"Telegram channel not found: {name}")

    token = str(cfg.get("bot_token") or "")
    if not token:
        raise RuntimeError("Telegram bot token is missing")

    try:
        info = httpx.post(_api(token, "getWebhookInfo"), timeout=4)
        info.raise_for_status()
        webhook_url = ((info.json().get("result") or {}).get("url") or "").strip()
        if webhook_url:
            raise RuntimeError(
                "Telegram bot currently has a webhook configured. "
                "getUpdates long polling cannot run until that webhook is removed."
            )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Cannot verify Telegram bot configuration: {exc}") from exc

    offset: int | None = None
    print(f"human:// Telegram channel '{name}' listening")
    print("Press Ctrl+C to stop.")

    while True:
        payload: dict[str, Any] = {
            "timeout": 30,
            "allowed_updates": ["callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset

        try:
            response = httpx.post(
                _api(token, "getUpdates"),
                json=payload,
                timeout=35,
            )
            response.raise_for_status()
            data = response.json()
            for update in data.get("result") or []:
                uid = int(update.get("update_id", 0))
                offset = max(offset or 0, uid + 1)
                query = update.get("callback_query")
                if query:
                    handle_callback(name, cfg, query)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            print(f"telegram channel error: {exc}")
            time.sleep(2)
