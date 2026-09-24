from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any

import httpx

from humanqueue.client import HumanQueue
from humanqueue.config import gateway_token, gateway_url
from .base import ContextCapsule, NativeHandle


def _headers() -> dict[str, str]:
    token = gateway_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _event_ref(event: dict[str, Any]) -> str:
    raw = json.dumps(
        {
            "session_id": event.get("session_id"),
            "prompt_id": event.get("prompt_id"),
            "tool_name": event.get("tool_name"),
            "tool_input": event.get("tool_input"),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def observe_event(event: dict[str, Any]) -> None:
    session_id = event.get("session_id")
    if not session_id:
        return

    event_name = str(event.get("hook_event_name") or "unknown")
    latest_user = event.get("prompt") if event_name == "UserPromptSubmit" else None
    latest_assistant = event.get("last_assistant_message") if event_name == "Stop" else None

    payload = {
        "provider": "claude-code",
        "event_name": event_name,
        "session_id": str(session_id),
        "turn_id": event.get("prompt_id"),
        "cwd": event.get("cwd"),
        "model": event.get("model") or event.get("to_model"),
        "transcript_path": event.get("transcript_path"),
        "latest_user_prompt": latest_user,
        "latest_assistant_message": latest_assistant,
        "tool_name": event.get("tool_name"),
        "tool_use_id": event.get("tool_use_id"),
        "tool_input": event.get("tool_input"),
        "metadata": {
            "permission_mode": event.get("permission_mode"),
            "scratchpad_dir": event.get("scratchpad_dir"),
            "hook_event_name": event_name,
        },
    }
    try:
        httpx.post(
            gateway_url() + "/v1/connectors/events",
            json=payload,
            headers=_headers(),
            timeout=0.8,
        )
    except Exception:
        pass


def permission_request(event: dict[str, Any]) -> dict[str, Any]:
    observe_event(event)

    tool_name = str(event.get("tool_name") or "tool")
    tool_input = event.get("tool_input") or {}
    description = tool_input.get("description") if isinstance(tool_input, dict) else None
    command = tool_input.get("command") if isinstance(tool_input, dict) else None

    title = description or (
        f"Allow Claude Code {tool_name}: {command}" if command
        else f"Allow Claude Code {tool_name}?"
    )

    handle = NativeHandle(
        provider="claude-code",
        session_id=str(event.get("session_id") or "unknown"),
        turn_id=event.get("prompt_id"),
        transcript_locator=event.get("transcript_path"),
        resume_kind="claude_permission_request",
        extra={"permission_mode": event.get("permission_mode")},
    )
    capsule = ContextCapsule(
        provider="claude-code",
        session_id=handle.session_id,
        cwd=event.get("cwd"),
        model=event.get("model"),
        turn_id=handle.turn_id,
        event_name="PermissionRequest",
        tool_name=tool_name,
        tool_input=tool_input,
        transcript_locator=event.get("transcript_path"),
        native_handle=handle,
    )

    client = HumanQueue()
    timeout = float(os.environ.get("HUMAN_QUEUE_HOOK_WAIT_SECONDS", "570"))
    try:
        decision = client.ask(
            "human://approve",
            source="claude-code",
            ref=_event_ref(event),
            title=title[:240],
            summary="Claude Code paused at its native PermissionRequest boundary.",
            why_now=description or "Claude Code needs a permission decision before this tool can continue.",
            context=capsule.to_dict(),
            urgency=0.75,
            unblock=0.95,
            risk=0.85,
            seconds=8,
            downstream=1,
            idempotency_key="claude:" + _event_ref(event),
            supersession_key=f"claude:{event.get('session_id')}:{event.get('prompt_id')}:{tool_name}",
            wait=True,
            wait_timeout=timeout,
            poll_interval=0.8,
        )
    except Exception:
        # No structured decision => Claude Code continues through its normal
        # permission flow. Never fail open.
        return {}

    action = str(decision.get("action") or "").lower()
    if action in {"approve", "allow", "accept", "continue"}:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "allow"},
            }
        }
    if action in {"reject", "deny", "decline", "cancel"}:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {
                    "behavior": "deny",
                    "message": str(decision.get("comment") or "Denied in Human Queue."),
                },
            }
        }
    return {}


def hook_main(mode: str) -> int:
    try:
        event = json.load(sys.stdin)
    except Exception:
        print("{}")
        return 0

    if mode == "claude-permission":
        result = permission_request(event)
    elif mode == "claude-observe":
        observe_event(event)
        result = {}
    else:
        result = {}

    sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\n")
    return 0


def _command(mode: str) -> str:
    return f"{shlex.quote(sys.executable)} -m humanqueue connector hook {mode}"


def install_claude_hooks() -> dict[str, Any]:
    claude_dir = Path.home() / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    path = claude_dir / "settings.json"

    if path.exists():
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Cannot parse existing {path}: {exc}") from exc
        backup = path.with_suffix(".json.humanq.bak")
        if not backup.exists():
            shutil.copy2(path, backup)
    else:
        config = {}

    hooks = config.setdefault("hooks", {})

    def strip(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cleaned = []
        for group in entries:
            handlers = group.get("hooks", [])
            kept = [
                h for h in handlers
                if "humanqueue connector hook" not in str(h.get("command", "")).replace("-m ", "")
                and "humanq connector hook" not in str(h.get("command", ""))
            ]
            if kept:
                clone = dict(group)
                clone["hooks"] = kept
                cleaned.append(clone)
        return cleaned

    for event_name in ("PermissionRequest", "SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"):
        hooks[event_name] = strip(list(hooks.get(event_name, [])))

    hooks["PermissionRequest"].append({
        "matcher": "*",
        "hooks": [{
            "type": "command",
            "command": _command("claude-permission"),
            "timeout": 600,
        }],
    })

    observer = {
        "type": "command",
        "command": _command("claude-observe"),
        "timeout": 2,
    }
    for event_name in ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"):
        hooks[event_name].append({"hooks": [dict(observer)]})

    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return {
        "provider": "claude-code",
        "settings_path": str(path),
        "claude_detected": bool(shutil.which("claude")),
        "installed_events": [
            "PermissionRequest",
            "SessionStart",
            "UserPromptSubmit",
            "Stop",
            "SessionEnd",
        ],
    }


def uninstall_claude_hooks() -> dict[str, Any]:
    path = Path.home() / ".claude" / "settings.json"
    if not path.exists():
        return {"provider": "claude-code", "removed": False}

    config = json.loads(path.read_text(encoding="utf-8"))
    hooks = config.get("hooks", {})
    changed = False

    for event_name, entries in list(hooks.items()):
        new_entries = []
        for group in entries:
            handlers = group.get("hooks", [])
            kept = [
                h for h in handlers
                if "humanqueue connector hook" not in str(h.get("command", "")).replace("-m ", "")
                and "humanq connector hook" not in str(h.get("command", ""))
            ]
            if len(kept) != len(handlers):
                changed = True
            if kept:
                clone = dict(group)
                clone["hooks"] = kept
                new_entries.append(clone)
        hooks[event_name] = new_entries

    if changed:
        path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return {"provider": "claude-code", "removed": changed, "settings_path": str(path)}
