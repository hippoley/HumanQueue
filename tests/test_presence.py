from __future__ import annotations

from pathlib import Path

from app.presence_registry import PresenceRegistry, PresenceUpdate


def test_presence_registry_keeps_accounts_separate(tmp_path: Path):
    reg = PresenceRegistry(str(tmp_path / "presence.db"))

    reg.upsert_source(
        source_id="openclaw:work",
        provider="openclaw",
        account="work",
        mode="gateway-ws",
        endpoint="ws://openclaw-work:18789",
        config={"credential_env": "OPENCLAW_WORK_TOKEN"},
    )
    reg.upsert_source(
        source_id="openclaw:personal",
        provider="openclaw",
        account="personal",
        mode="gateway-ws",
        endpoint="ws://openclaw-personal:18789",
        config={"credential_env": "OPENCLAW_PERSONAL_TOKEN"},
    )

    reg.update(PresenceUpdate(
        source_id="openclaw:work",
        provider="openclaw",
        account="work",
        session_id="same-session-key",
        state="running",
        title="Release",
        current_action="deploy",
    ))
    reg.update(PresenceUpdate(
        source_id="openclaw:personal",
        provider="openclaw",
        account="personal",
        session_id="same-session-key",
        state="waiting_human",
        title="Trip planning",
        waiting_reason="choice required",
    ))

    rows = reg.sessions()
    assert len(rows) == 2
    by_source = {row["source_id"]: row for row in rows}
    assert by_source["openclaw:work"]["state"] == "running"
    assert by_source["openclaw:personal"]["state"] == "waiting_human"


def test_presence_update_preserves_context_but_clears_wait_reason(tmp_path: Path):
    reg = PresenceRegistry(str(tmp_path / "presence.db"))
    reg.update(PresenceUpdate(
        source_id="muse:local",
        provider="muse",
        account="local",
        session_id="muse-1",
        state="waiting_human",
        title="Refactor auth",
        last_user="Finish the migration",
        last_agent="Need permission to write config",
        current_action="protected-write",
        waiting_reason="approval required",
    ))

    row = reg.update(PresenceUpdate(
        source_id="muse:local",
        provider="muse",
        account="local",
        session_id="muse-1",
        state="running",
        current_action="write config",
        waiting_reason=None,
    ))

    assert row["title"] == "Refactor auth"
    assert row["last_user"] == "Finish the migration"
    assert row["last_agent"] == "Need permission to write config"
    assert row["waiting_reason"] is None
    assert row["state"] == "running"
