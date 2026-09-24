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
    assert names == ["human_ask", "human_presence_list", "human_presence_summary"]


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


def test_channel_callback_resolves_once_and_resumes(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    from app.models import AttentionRequestCreate, RequestKind
    from app.store import Store

    main.store = Store(str(tmp_path / "channel-roundtrip.db"))
    item = main.store.create(AttentionRequestCreate(
        source="codex",
        source_ref="thr_1:turn_1:Bash",
        title="Allow git push?",
        summary="Native editor is waiting.",
        kind=RequestKind.approval,
    ))

    monkeypatch.setattr(main, "verify_resolution", lambda name, body, signature: name == "ops")

    resumed = {}

    async def fake_resume(req, resolution):
        resumed["request_id"] = req.id
        resumed["action"] = resolution.get("action")
        return {"delivered": True, "test": True}

    monkeypatch.setattr(main, "resume", fake_resume)
    client = TestClient(main.app)

    first = client.post(
        f"/channels/ops/resolve/{item.id}",
        headers={"X-Human-Channel-Signature": "sha256=test"},
        json={"actor": "channel:ops", "action": "approve"},
    )
    assert first.status_code == 200
    assert first.json()["finalized"] is True
    assert first.json()["resume"]["delivered"] is True
    assert resumed == {"request_id": item.id, "action": "approve"}

    second = client.post(
        f"/channels/ops/resolve/{item.id}",
        headers={"X-Human-Channel-Signature": "sha256=test"},
        json={"actor": "channel:ops", "action": "approve"},
    )
    assert second.status_code == 409


def test_gateway_enriches_permission_with_latest_dialogue(tmp_path: Path):
    from app.context_enrichment import enrich_with_session_context
    from app.models import AttentionRequestCreate, RequestKind

    reg = ConnectorRegistry(str(tmp_path / "enrich.db"))
    reg.record(ConnectorEventIn(
        provider="codex",
        event_name="UserPromptSubmit",
        session_id="thr_dialogue",
        turn_id="turn_9",
        cwd="/repo",
        latest_user_prompt="Deploy this only if CI is green.",
    ))
    reg.record(ConnectorEventIn(
        provider="codex",
        event_name="Stop",
        session_id="thr_dialogue",
        turn_id="turn_9",
        latest_assistant_message="CI is green; preparing git push.",
    ))

    req = AttentionRequestCreate(
        source="codex",
        source_ref="native-1",
        title="Allow git push?",
        summary="Native permission boundary.",
        kind=RequestKind.approval,
        context={
            "native_handle": {
                "provider": "codex",
                "session_id": "thr_dialogue",
                "turn_id": "turn_9",
                "resume_kind": "codex_permission_request",
            }
        },
    )
    enriched = enrich_with_session_context(req, reg)
    session = enriched.context["session_context"]
    assert session["latest_user_prompt"] == "Deploy this only if CI is green."
    assert session["latest_assistant_message"] == "CI is green; preparing git push."


def test_webhook_projection_includes_bounded_dialogue_not_transcript():
    from humanqueue.channels.webhook import _bounded_context

    projected = _bounded_context({
        "session_context": {
            "provider": "codex",
            "session_id": "thr_1",
            "latest_user_prompt": "Please deploy after tests.",
            "latest_assistant_message": "Tests passed.",
            "transcript_locator": "/private/transcript.jsonl",
        }
    })
    assert projected["session_context"]["latest_user_prompt"] == "Please deploy after tests."
    assert projected["session_context"]["latest_assistant_message"] == "Tests passed."
    assert "transcript_locator" not in projected["session_context"]


def test_telegram_message_contains_bounded_dialogue(tmp_path: Path):
    from app.models import AttentionRequestCreate, RequestKind
    from app.store import Store
    from humanqueue.channels.telegram import render_message

    s = Store(str(tmp_path / "telegram-message.db"))
    item = s.create(AttentionRequestCreate(
        source="codex",
        source_ref="native-telegram",
        title="Allow production deploy?",
        summary="Codex is waiting.",
        kind=RequestKind.approval,
        context={
            "session_context": {
                "provider": "codex",
                "session_id": "thr_tg",
                "latest_user_prompt": "Deploy only after CI.",
                "latest_assistant_message": "CI is green.",
                "transcript_locator": "/private/transcript.jsonl",
            }
        },
    ))
    text, markup = render_message(item)
    assert "Deploy only after CI." in text
    assert "CI is green." in text
    assert "/private/transcript.jsonl" not in text
    assert markup["inline_keyboard"]


def test_telegram_callback_parser_and_authorized_resolution(monkeypatch):
    from humanqueue.channels import telegram

    assert telegram.parse_callback_data("hq|attn_123|approve") == ("attn_123", "approve")
    assert telegram.parse_callback_data("bad|attn_123|approve") is None

    called = {}
    monkeypatch.setattr(
        telegram,
        "_resolve_local",
        lambda rid, action, actor: (
            called.update({"rid": rid, "action": action, "actor": actor}) or True,
            "Resolved in Human Queue",
        ),
    )

    class DummyResponse:
        status_code = 200
        content = b'{}'
        is_success = True
        def json(self):
            return {"ok": True}

    monkeypatch.setattr(telegram.httpx, "post", lambda *a, **k: DummyResponse())

    ok, message = telegram.handle_callback(
        "phone",
        {"type": "telegram", "bot_token": "bot-secret", "chat_id": "42"},
        {
            "id": "callback-1",
            "data": "hq|attn_123|approve",
            "from": {"id": 7},
            "message": {"message_id": 99, "chat": {"id": 42}},
        },
    )
    assert ok is True
    assert called == {"rid": "attn_123", "action": "approve", "actor": "telegram:7"}


def test_telegram_rejects_callback_from_other_chat(monkeypatch):
    from humanqueue.channels import telegram

    monkeypatch.setattr(
        telegram,
        "_resolve_local",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not resolve")),
    )

    class DummyResponse:
        status_code = 200
        content = b'{}'
        is_success = True
        def json(self):
            return {"ok": True}

    monkeypatch.setattr(telegram.httpx, "post", lambda *a, **k: DummyResponse())

    ok, message = telegram.handle_callback(
        "phone",
        {"type": "telegram", "bot_token": "bot-secret", "chat_id": "42"},
        {
            "id": "callback-2",
            "data": "hq|attn_123|approve",
            "from": {"id": 7},
            "message": {"message_id": 100, "chat": {"id": 999}},
        },
    )
    assert ok is False
    assert "not authorized" in message


def test_claude_permission_round_trip_allow(monkeypatch):
    from humanqueue.connectors import claude

    class FakeHumanQueue:
        def ask(self, *args, **kwargs):
            assert args[0] == "human://approve"
            assert kwargs["source"] == "claude-code"
            return {"action": "approve"}

    monkeypatch.setattr(claude, "HumanQueue", FakeHumanQueue)
    monkeypatch.setattr(claude, "observe_event", lambda event: None)

    result = claude.permission_request({
        "session_id": "claude_1",
        "prompt_id": "prompt_1",
        "hook_event_name": "PermissionRequest",
        "tool_name": "Bash",
        "tool_input": {"command": "git push"},
        "cwd": "/repo",
    })
    decision = result["hookSpecificOutput"]["decision"]
    assert decision["behavior"] == "allow"


def test_claude_permission_falls_back_to_native_prompt(monkeypatch):
    from humanqueue.connectors import claude

    class FakeHumanQueue:
        def ask(self, *args, **kwargs):
            raise TimeoutError("gateway unavailable")

    monkeypatch.setattr(claude, "HumanQueue", FakeHumanQueue)
    monkeypatch.setattr(claude, "observe_event", lambda event: None)

    assert claude.permission_request({
        "session_id": "claude_1",
        "prompt_id": "prompt_1",
        "hook_event_name": "PermissionRequest",
        "tool_name": "Bash",
        "tool_input": {"command": "git push"},
    }) == {}


def test_opencode_installer_writes_global_plugin(tmp_path: Path):
    from humanqueue.connectors.opencode import install_opencode_plugin, uninstall_opencode_plugin

    result = install_opencode_plugin(tmp_path)
    target = Path(result["plugin_path"])
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    assert 'Plugin.define' in content
    assert 'ctx.permission.hook("evaluate"' in content
    assert 'event.effect !== "ask"' in content

    removed = uninstall_opencode_plugin(tmp_path)
    assert removed["removed"] is True
    assert not target.exists()


def test_slack_decision_value_and_bounded_blocks(tmp_path: Path):
    from app.models import AttentionRequestCreate, RequestKind
    from app.store import Store
    from humanqueue.channels.slack import parse_decision_value, render_blocks

    assert parse_decision_value("attn_1|approve") == ("attn_1", "approve")
    assert parse_decision_value("broken") is None

    store = Store(str(tmp_path / "slack.db"))
    item = store.create(AttentionRequestCreate(
        source="claude-code",
        source_ref="permission-1",
        title="Allow git push?",
        summary="Claude Code is waiting.",
        kind=RequestKind.approval,
        context={
            "session_context": {
                "provider": "claude-code",
                "session_id": "claude_1",
                "latest_user_prompt": "Push only after CI passes.",
                "latest_assistant_message": "CI passed.",
                "transcript_locator": "/private/full.jsonl",
            }
        },
    ))
    blocks = render_blocks(item)
    encoded = str(blocks)
    assert "Push only after CI passes." in encoded
    assert "CI passed." in encoded
    assert "/private/full.jsonl" not in encoded
    assert "humanq_decision" in encoded


def test_mcp_presence_summary(monkeypatch):
    class DummyResponse:
        def raise_for_status(self):
            return None
        def json(self):
            return {
                "sessions": [
                    {"provider": "openclaw", "account": "work", "state": "running", "session_id": "a"},
                    {"provider": "muse", "account": "local", "state": "waiting_human", "session_id": "b"},
                    {"provider": "cursor", "account": "local", "state": "failed", "session_id": "c"},
                ]
            }

    monkeypatch.setattr(mcp_server.httpx, "get", lambda *a, **k: DummyResponse())
    response = mcp_server.handle({
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {"name": "human_presence_summary", "arguments": {}},
    })
    structured = response["result"]["structuredContent"]
    assert structured["total"] == 3
    assert structured["counts"]["running"] == 1
    assert structured["counts"]["waiting_human"] == 1
    assert len(structured["needs_attention"]) == 2
