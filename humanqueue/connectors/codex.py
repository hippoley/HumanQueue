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
    session = event.get("session_id") or "unknown"
    turn = event.get("turn_id") or event.get("tool_use_id") or "turn"
    tool = event.get("tool_name") or "tool"
    raw = json.dumps(event.get("tool_input"), sort_keys=True, default=str)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:12]
    return f"{session}:{turn}:{tool}:{digest}"


def _event_identity_is_stable(event: dict[str, Any]) -> bool:
    return bool(event.get("session_id") and (event.get("turn_id") or event.get("tool_use_id")))


def capsule_from_event(event: dict[str, Any]) -> ContextCapsule:
    session_id = str(event.get("session_id") or "unknown")
    handle = NativeHandle(
        provider="codex",
        session_id=session_id,
        turn_id=event.get("turn_id"),
        tool_use_id=event.get("tool_use_id"),
        transcript_locator=event.get("transcript_path"),
        resume_kind="codex_permission_request",
        extra={"permission_mode": event.get("permission_mode")},
    )
    return ContextCapsule(
        provider="codex",
        session_id=session_id,
        cwd=event.get("cwd"),
        model=event.get("model"),
        turn_id=event.get("turn_id"),
        event_name=event.get("hook_event_name"),
        latest_user_intent=event.get("prompt"),
        latest_assistant_message=event.get("last_assistant_message"),
        tool_name=event.get("tool_name"),
        tool_input=event.get("tool_input"),
        transcript_locator=event.get("transcript_path"),
        native_handle=handle,
    )


def observe_event(event: dict[str, Any]) -> None:
    session_id = event.get("session_id")
    if not session_id:
        return
    payload = {
        "provider": "codex",
        "event_name": event.get("hook_event_name") or "unknown",
        "session_id": str(session_id),
        "turn_id": event.get("turn_id"),
        "cwd": event.get("cwd"),
        "model": event.get("model"),
        "transcript_path": event.get("transcript_path"),
        "latest_user_prompt": event.get("prompt"),
        "latest_assistant_message": event.get("last_assistant_message"),
        "tool_name": event.get("tool_name"),
        "tool_use_id": event.get("tool_use_id"),
        "tool_input": event.get("tool_input"),
        "metadata": {
            "permission_mode": event.get("permission_mode"),
            "hook_event_name": event.get("hook_event_name"),
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
        # Observability must never break the native agent.
        pass


def permission_request(event: dict[str, Any]) -> dict[str, Any]:
    observe_event(event)

    tool_name = str(event.get("tool_name") or "tool")
    tool_input = event.get("tool_input") or {}
    description = tool_input.get("description") if isinstance(tool_input, dict) else None
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    title = description or (f"Allow Codex {tool_name}: {command}" if command else f"Allow Codex {tool_name}?")
    summary = "Codex paused at its native approval boundary and is waiting for your decision."

    capsule = capsule_from_event(event)
    ref = _event_ref(event)
    stable_identity = _event_identity_is_stable(event)
    native_turn = event.get("turn_id") or event.get("tool_use_id")

    client = HumanQueue()
    timeout = float(os.environ.get("HUMAN_QUEUE_HOOK_WAIT_SECONDS", "570"))
    try:
        decision = client.ask(
            "human://approve",
            source="codex",
            ref=ref,
            title=title[:240],
            summary=summary,
            why_now=description or "Codex cannot continue this tool call until approval is resolved.",
            context=capsule.to_dict(),
            urgency=0.75,
            unblock=0.95,
            risk=0.85,
            seconds=8,
            downstream=1,
            idempotency_key=("codex:" + ref) if stable_identity else None,
            supersession_key=(
                f"codex:{event.get('session_id')}:{native_turn}:{tool_name}"
                if stable_identity else None
            ),
            wait=True,
            wait_timeout=timeout,
            poll_interval=0.8,
        )
    except Exception:
        # Empty output means this hook declines to decide and Codex can use its
        # normal native approval prompt instead.
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
        message = decision.get("comment") or "Denied in Human Queue."
        return {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "deny", "message": str(message)},
            }
        }
    return {}


def hook_main(mode: str) -> int:
    try:
        event = json.load(sys.stdin)
    except Exception:
        print("{}")
        return 0

    if mode == "codex-permission":
        result = permission_request(event)
    elif mode == "codex-observe":
        observe_event(event)
        result = {}
    else:
        result = {}

    sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\n")
    return 0


def _humanq_command(mode: str) -> str:
    python = shlex.quote(sys.executable)
    return f"{python} -m humanqueue connector hook {mode}"


def install_codex_hooks() -> dict[str, Any]:
    codex_dir = Path.home() / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    path = codex_dir / "hooks.json"

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

    def strip_humanq(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
        hooks[event_name] = strip_humanq(list(hooks.get(event_name, [])))

    hooks["PermissionRequest"].append({
        "matcher": ".*",
        "hooks": [{
            "type": "command",
            "command": _humanq_command("codex-permission"),
            "timeout": 600,
            "statusMessage": "Waiting for Human Queue",
        }],
    })

    observer = {
        "type": "command",
        "command": _humanq_command("codex-observe"),
        "timeout": 2,
    }
    for event_name in ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"):
        hooks[event_name].append({"hooks": [dict(observer)]})

    config["description"] = config.get("description") or "Codex hooks including human:// Human Queue connector."
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return {
        "provider": "codex",
        "hooks_path": str(path),
        "codex_detected": bool(shutil.which("codex")),
        "installed_events": ["PermissionRequest", "SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"],
        "requires_trust_review": True,
    }


def uninstall_codex_hooks() -> dict[str, Any]:
    path = Path.home() / ".codex" / "hooks.json"
    if not path.exists():
        return {"provider": "codex", "removed": False}

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
    return {"provider": "codex", "removed": changed, "hooks_path": str(path)}
