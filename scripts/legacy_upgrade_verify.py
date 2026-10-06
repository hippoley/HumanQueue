from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from app.store import Store


def main() -> None:
    db = Path(os.environ["HUMAN_QUEUE_LEGACY_DB"]).resolve()
    manifest = json.loads(
        Path(os.environ["HUMAN_QUEUE_LEGACY_MANIFEST"]).read_text(encoding="utf-8")
    )
    version = str(manifest["version"])

    store = Store(str(db))

    with sqlite3.connect(db) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "channel_outbox" in tables, tables
    assert "resume_outbox" in tables, tables

    budget = store.get_budget("legacy-team")
    assert budget.max_interrupts_per_hour == 3
    assert budget.min_interrupt_priority == 80

    pending = store.get(manifest["pending_id"])
    quorum = store.get(manifest["quorum_id"])
    already_resolved = store.get(manifest["resolved_id"])

    assert pending is not None and pending.status.value == "pending"
    assert quorum is not None and quorum.status.value == "claimed"
    assert already_resolved is not None and already_resolved.status.value == "resolved"

    # Historical records must not be silently rewritten merely because the
    # current Store opened the database.
    assert len(store.events(already_resolved.id)) == manifest["legacy_event_counts"][already_resolved.id]

    # A legacy pending webhook-backed request should continue through the
    # current terminal path and gain today's durable resume intent.
    pending_after, pending_finalized = store.resolve(
        pending.id,
        "carol",
        {
            "action": "approve",
            "values": {"upgraded": True},
            "comment": "resolved after upgrade",
        },
        actor_kind="human",
    )
    assert pending_finalized is True
    assert pending_after is not None
    assert pending_after.status.value == "resolved"
    assert pending_after.resolution is not None
    assert pending_after.resolution["provenance"]["actor_kind"] == "human"

    resume = store.resume_outbox(pending.id)
    assert resume is not None
    assert resume["status"] == "pending"
    assert int(resume["attempts"]) == 0

    # The old first quorum vote must still count after upgrade.
    quorum_before = store.get(quorum.id)
    assert quorum_before is not None
    assert quorum_before.quorum_progress["votes"] == 1
    assert quorum_before.quorum_progress["actors"] == ["alice"]

    quorum_after, quorum_finalized = store.resolve(
        quorum.id,
        "bob",
        {
            "action": "approve",
            "values": {"target": "prod"},
            "comment": "current second vote",
        },
        actor_kind="human",
    )
    assert quorum_finalized is True
    assert quorum_after is not None
    assert quorum_after.status.value == "resolved"
    assert quorum_after.resolution is not None

    # A current canonical resolution must never lose the typed-provenance
    # invariant merely because the winning quorum includes a legacy vote.
    provenance = quorum_after.resolution.get("provenance")
    assert provenance is not None, quorum_after.resolution
    assert provenance.get("actor_kind") == "human", quorum_after.resolution

    events = store.events(quorum.id)
    assert len([e for e in events if e["type"] == "resolved"]) == 1
    assert len([e for e in events if e["type"] == "vote"]) == 2

    print(f"LEGACY_{version.replace('.', '_')}_TO_CURRENT_UPGRADE_OK")
    print("pending_resume_outbox=" + str(resume["status"]))
    print("quorum_provenance=" + json.dumps(provenance, sort_keys=True))


if __name__ == "__main__":
    main()
