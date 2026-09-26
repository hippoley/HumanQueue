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


RISKY_SHELL_MATCHER = (
    r"rm\s+-rf|git\s+push|kubectl\s+(delete|apply)|terraform\s+apply|"
    r"docker\s+system\s+prune|DROP\s+TABLE|DELETE\s+FROM|"
    r"curl.*-X\s+(POST|PUT|PATCH|DELETE)"
)


def _headers() -> dict[str, str]:
    token = gateway_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _session_id(event: dict[str, Any]) -> str:
    return str(event.get("conversation_id") or event.get("session_id") or "unknown")


def _turn_id(event: dict[str, Any]) -> str | None:
    value = event.get("generation_id") or event.get("turn_id")
    return str(value) if value is not None else None


def _event_ref(event: dict[str, Any]) -> str:
    raw = json.dumps(
        {
            "conversation_id": event.get("conversation_id"),
            "generation_id": event.get("generation_id"),
            "command": event.get("command"),
            "cwd": event.get("cwd"),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def _event_identity_is_stable(event: dict[str, Any]) -> bool:
    return _session_id(event) != "unknown" and _turn_id(event) is not None


def observe_event(event: dict[str, Any]) -> None:
    session = _session_id(event)
    if session == "unknown":
        return
    event_name = str(event.get("hook_event_name") or "unknown")
    latest_user = event.get("prompt") if event_name == "beforeSubmitPrompt" else None
    latest_assistant = event.get("text") if event_name == "afterAgentResponse" else None
    payload = {
        "provider": "cursor",
        "event_name": event_name,
        "session_id": session,
        "turn_id": _turn_id(event),
        "cwd": event.get("cwd") or ((event.get("workspace_roots") or [None])[0]),
        "model": event.get("model_id") or event.get("model"),
        "transcript_path": event.get("transcript_path"),
        "latest_user_prompt": latest_user,
        "latest_assistant_message": latest_assistant,
        "tool_name": "Shell" if event_name == "beforeShellExecution" else event.get("tool_name"),
        "tool_use_id": event.get("tool_use_id"),
        "tool_input": {"command": event.get("command")} if event.get("command") else event.get("tool_input"),
        "metadata": {
            "cursor_version": event.get("cursor_version"),
            "user_email": event.get("user_email"),
            "workspace_roots": event.get("workspace_roots"),
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


def shell_permission(event: dict[str, Any]) -> dict[str, Any]:
    observe_event(event)
    session = _session_id(event)
    turn = _turn_id(event)
    command = str(event.get("command") or "")
    handle = NativeHandle(
        provider="cursor",
        session_id=session,
        turn_id=turn,
        transcript_locator=event.get("transcript_path"),
        resume_kind="cursor_shell_permission",
        extra={"generation_id": event.get("generation_id")},
    )
    capsule = ContextCapsule(
        provider="cursor",
        session_id=session,
        cwd=event.get("cwd") or ((event.get("workspace_roots") or [None])[0]),
        model=event.get("model_id") or event.get("model"),
        turn_id=turn,
        event_name="beforeShellExecution",
        tool_name="Shell",
        tool_input={"command": command},
        transcript_locator=event.get("transcript_path"),
        native_handle=handle,
    )

    stable_identity = _event_identity_is_stable(event)

    client = HumanQueue()
    timeout = float(os.environ.get("HUMAN_QUEUE_HOOK_WAIT_SECONDS", "570"))
    try:
        decision = client.ask(
            "human://approve",
            source="cursor",
            ref=_event_ref(event),
            title=f"Allow Cursor shell command: {command}"[:240],
            summary="Cursor paused a high-risk shell command before execution.",
            why_now="The command matched the human:// high-risk shell gate.",
            context=capsule.to_dict(),
            urgency=0.8,
            unblock=0.95,
            risk=0.9,
            seconds=8,
            downstream=1,
            idempotency_key=("cursor:" + _event_ref(event)) if stable_identity else None,
            wait=True,
            wait_timeout=timeout,
            poll_interval=0.8,
        )
    except Exception:
        return {
            "permission": "ask",
            "user_message": "human:// unavailable; falling back to Cursor approval.",
        }

    action = str(decision.get("action") or "").lower()
    if action in {"approve", "allow", "accept", "continue"}:
        return {"permission": "allow"}
    if action in {"reject", "deny", "decline", "cancel"}:
        message = str(decision.get("comment") or "Denied in human://.")
        return {
            "permission": "deny",
            "user_message": message,
            "agent_message": message,
        }
    return {"permission": "ask"}


def hook_main(mode: str) -> int:
    try:
        event = json.load(sys.stdin)
    except Exception:
        print("{}")
        return 0

    if mode == "cursor-shell":
        result = shell_permission(event)
    elif mode == "cursor-observe":
        observe_event(event)
        result = {}
    else:
        result = {}

    sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\n")
    return 0


def _command(mode: str) -> str:
    return f"{shlex.quote(sys.executable)} -m humanqueue connector hook {mode}"


def install_cursor_hooks() -> dict[str, Any]:
    cursor_dir = Path.home() / ".cursor"
    cursor_dir.mkdir(parents=True, exist_ok=True)
    path = cursor_dir / "hooks.json"

    if path.exists():
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Cannot parse existing {path}: {exc}") from exc
        backup = path.with_suffix(".json.humanq.bak")
        if not backup.exists():
            shutil.copy2(path, backup)
    else:
        config = {"version": 1}

    config.setdefault("version", 1)
    hooks = config.setdefault("hooks", {})

    def strip(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            e for e in entries
            if "humanqueue connector hook" not in str(e.get("command", "")).replace("-m ", "")
            and "humanq connector hook" not in str(e.get("command", ""))
        ]

    for name in ("sessionStart", "sessionEnd", "beforeSubmitPrompt", "afterAgentResponse", "beforeShellExecution"):
        hooks[name] = strip(list(hooks.get(name, [])))

    observer = {"command": _command("cursor-observe"), "timeout": 2}
    for name in ("sessionStart", "sessionEnd", "beforeSubmitPrompt", "afterAgentResponse"):
        hooks[name].append(dict(observer))

    hooks["beforeShellExecution"].append({
        "command": _command("cursor-shell"),
        "matcher": RISKY_SHELL_MATCHER,
        "timeout": 600,
        "failClosed": False,
    })

    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return {
        "provider": "cursor",
        "hooks_path": str(path),
        "cursor_detected": bool(shutil.which("cursor")),
        "observed_events": ["sessionStart", "sessionEnd", "beforeSubmitPrompt", "afterAgentResponse"],
        "gated_event": "beforeShellExecution",
        "matcher": RISKY_SHELL_MATCHER,
    }


def uninstall_cursor_hooks() -> dict[str, Any]:
    path = Path.home() / ".cursor" / "hooks.json"
    if not path.exists():
        return {"provider": "cursor", "removed": False}
    config = json.loads(path.read_text(encoding="utf-8"))
    hooks = config.get("hooks", {})
    changed = False
    for name, entries in list(hooks.items()):
        kept = []
        for entry in entries:
            cmd = str(entry.get("command", ""))
            is_hq = (
                "humanqueue connector hook" in cmd.replace("-m ", "")
                or "humanq connector hook" in cmd
            )
            if is_hq:
                changed = True
            else:
                kept.append(entry)
        hooks[name] = kept
    if changed:
        path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return {"provider": "cursor", "removed": changed, "hooks_path": str(path)}
