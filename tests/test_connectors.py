from __future__ import annotations

import io
import json
from pathlib import Path

from app.connector_registry import ConnectorEventIn, ConnectorRegistry
from humanqueue.connectors.base import ContextCapsule, NativeHandle
from humanqueue.connectors import codex
from humanqueue import mcp_server


def test_connector_registry_tracks_one_session(tmp_path: Path):
    reg = ConnectorRegistry(str(tmp_path / "sessions.db"))
    reg.record(ConnectorEventIn(
        provider="codex",
        event_name="UserPromptSubmit",
        session_id="thr_1",
        turn_id="turn_1",
        cwd="/repo",
        model="gpt-test",
        transcript_path="/repo/rollout.jsonl",
        latest_user_prompt="Ship it?",
    ))
    reg.record(ConnectorEventIn(
        provider="codex",
        event_name="Stop",
        session_id="thr_1",
        turn_id="turn_1",
        latest_assistant_message="Ready to deploy.",
    ))

    rows = reg.sessions()
    assert len(rows) == 1
    assert rows[0]["session_id"] == "thr_1"
    assert rows[0]["last_user_prompt"] == "Ship it?"
    assert rows[0]["last_assistant_message"] == "Ready to deploy."

    detail = reg.session("codex", "thr_1")
    assert detail is not None
    assert len(detail["events"]) == 2


def test_context_capsule_keeps_native_resume_handle():
    handle = NativeHandle(
        provider="codex",
        session_id="thr_1",
        turn_id="turn_1",
        resume_kind="codex_permission_request",
    )
    capsule = ContextCapsule(
        provider="codex",
        session_id="thr_1",
        turn_id="turn_1",
        tool_name="Bash",
        native_handle=handle,
    ).to_dict()
    assert capsule["native_handle"]["resume_kind"] == "codex_permission_request"


def test_codex_permission_round_trip_allow(monkeypatch):
    class FakeHumanQueue:
        def ask(self, *args, **kwargs):
            assert args[0] == "human://approve"
            assert kwargs["source"] == "codex"
            assert kwargs["wait"] is True
            return {"action": "approve"}

    monkeypatch.setattr(codex, "HumanQueue", FakeHumanQueue)
    monkeypatch.setattr(codex, "observe_event", lambda event: None)

    result = codex.permission_request({
        "session_id": "thr_1",
        "turn_id": "turn_1",
        "hook_event_name": "PermissionRequest",
        "tool_name": "Bash",
        "tool_input": {"command": "git push"},
        "cwd": "/repo",
    })
    assert result["hookSpecificOutput"]["decision"]["behavior"] == "allow"


def test_codex_permission_round_trip_deny(monkeypatch):
    class FakeHumanQueue:
        def ask(self, *args, **kwargs):
            return {"action": "reject", "comment": "No production push."}

    monkeypatch.setattr(codex, "HumanQueue", FakeHumanQueue)
    monkeypatch.setattr(codex, "observe_event", lambda event: None)

    result = codex.permission_request({
        "session_id": "thr_1",
        "turn_id": "turn_1",
        "hook_event_name": "PermissionRequest",
        "tool_name": "Bash",
        "tool_input": {"command": "git push"},
    })
    decision = result["hookSpecificOutput"]["decision"]
    assert decision["behavior"] == "deny"
    assert "production" in decision["message"]


def test_codex_permission_falls_back_when_gateway_is_unavailable(monkeypatch):
    class FakeHumanQueue:
        def ask(self, *args, **kwargs):
            raise TimeoutError("no human response")

    monkeypatch.setattr(codex, "HumanQueue", FakeHumanQueue)
    monkeypatch.setattr(codex, "observe_event", lambda event: None)

    assert codex.permission_request({
        "session_id": "thr_1",
        "turn_id": "turn_1",
        "tool_name": "Bash",
        "tool_input": {"command": "echo safe"},
    }) == {}


def test_mcp_server_initialize_and_tool_list():
    init = mcp_server.handle({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-06-18"},
    })
    assert init["result"]["capabilities"]["tools"] == {}

    tools = mcp_server.handle({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {},
    })
    names = [x["name"] for x in tools["result"]["tools"]]
    assert names == ["human_ask"]


def test_mcp_human_ask_returns_structured_decision(monkeypatch):
    monkeypatch.setattr(
        mcp_server,
        "_call_human",
        lambda arguments: {
            "content": [{"type": "text", "text": '{"status":"resolved"}'}],
            "structuredContent": {"status": "resolved"},
            "isError": False,
        },
    )
    response = mcp_server.handle({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "human_ask",
            "arguments": {
                "uri": "human://clarify",
                "title": "Which market?",
            },
        },
    })
    assert response["result"]["structuredContent"]["status"] == "resolved"


def test_cursor_shell_permission_round_trip_allow(monkeypatch):
    from humanqueue.connectors import cursor

    class FakeHumanQueue:
        def ask(self, *args, **kwargs):
            assert args[0] == "human://approve"
            assert kwargs["source"] == "cursor"
            return {"action": "approve"}

    monkeypatch.setattr(cursor, "HumanQueue", FakeHumanQueue)
    monkeypatch.setattr(cursor, "observe_event", lambda event: None)

    result = cursor.shell_permission({
        "conversation_id": "conv_1",
        "generation_id": "gen_1",
        "hook_event_name": "beforeShellExecution",
        "command": "git push origin main",
        "cwd": "/repo",
    })
    assert result["permission"] == "allow"


def test_cursor_shell_permission_falls_back_to_native_ask(monkeypatch):
    from humanqueue.connectors import cursor

    class FakeHumanQueue:
        def ask(self, *args, **kwargs):
            raise TimeoutError("no human response")

    monkeypatch.setattr(cursor, "HumanQueue", FakeHumanQueue)
    monkeypatch.setattr(cursor, "observe_event", lambda event: None)

    result = cursor.shell_permission({
        "conversation_id": "conv_1",
        "generation_id": "gen_1",
        "hook_event_name": "beforeShellExecution",
        "command": "rm -rf build",
    })
    assert result["permission"] == "ask"


def test_webhook_channel_signature(monkeypatch):
    from humanqueue.channels import webhook

    monkeypatch.setattr(
        webhook,
        "channel_configs",
        lambda: {
            "ops": {
                "type": "webhook",
                "secret": "hqc_test_secret",
                "url": "https://example.invalid/hook",
                "enabled": True,
            }
        },
    )
    body = b'{"action":"approve","actor":"alice"}'
    signature = webhook._sign("hqc_test_secret", body)
    assert webhook.verify_resolution("ops", body, signature)
    assert not webhook.verify_resolution("ops", body, "sha256=bad")


def test_webhook_channel_bounds_context():
    from humanqueue.channels.webhook import _bounded_context

    context = {
        "why_now": "needs review",
        "native_handle": {
            "provider": "codex",
            "session_id": "thr_1",
            "turn_id": "turn_1",
            "transcript_locator": "/private/full/transcript.jsonl",
            "extra": {"secret": "should-not-project"},
        },
        "tool_input": {
            "command": "git push",
            "secret_token": "do-not-send",
        },
        "unrelated_private_blob": "do-not-send",
    }
    projected = _bounded_context(context)
    assert projected["native_handle"]["session_id"] == "thr_1"
    assert "transcript_locator" not in projected["native_handle"]
    assert projected["tool_input"] == {"command": "git push"}
    assert "unrelated_private_blob" not in projected
