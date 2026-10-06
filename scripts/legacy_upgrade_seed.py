from __future__ import annotations

import json
import os
from pathlib import Path

from app.models import (
    AttentionRequestCreate,
    BudgetPolicy,
    RequestKind,
    ResumeTarget,
    RoutePolicy,
)
from app.store import Store


def make_request(
    *,
    source_ref: str,
    route: RoutePolicy | None = None,
    resume: ResumeTarget | None = None,
) -> AttentionRequestCreate:
    return AttentionRequestCreate(
        source=f"legacy-{version}",
        source_ref=source_ref,
        title=f"Legacy boundary {source_ref}",
        summary=f"Created by the real {version} package before upgrade.",
        kind=RequestKind.approval,
        route=route or RoutePolicy(),
        resume=resume or ResumeTarget(),
        idempotency_key=f"legacy:{source_ref}",
    )


def main() -> None:
    version = os.environ["HUMAN_QUEUE_LEGACY_VERSION"]
    db = Path(os.environ["HUMAN_QUEUE_LEGACY_DB"]).resolve()
    manifest_path = Path(os.environ["HUMAN_QUEUE_LEGACY_MANIFEST"]).resolve()
    db.parent.mkdir(parents=True, exist_ok=True)

    store = Store(str(db))
    store.set_budget(
        BudgetPolicy(
            group="legacy-team",
            max_interrupts_per_hour=3,
            min_interrupt_priority=80,
        )
    )

    pending = store.create(
        make_request(
            source_ref="pending-webhook",
            resume=ResumeTarget(
                mode="webhook",
                url="http://127.0.0.1:9999/legacy-resume",
                secret="legacy-secret",
            ),
        )
    )

    quorum = store.create(
        make_request(
            source_ref="partial-quorum",
            route=RoutePolicy(
                mode="quorum",
                actors=["alice", "bob"],
                quorum=2,
            ),
        )
    )
    quorum_after_alice, finalized = store.resolve(
        quorum.id,
        "alice",
        {
            "action": "approve",
            "values": {"target": "prod"},
            "comment": "legacy first vote",
        },
    )
    assert finalized is False
    assert quorum_after_alice is not None
    assert quorum_after_alice.status.value == "claimed"

    resolved = store.create(make_request(source_ref="already-resolved"))
    resolved_after, finalized = store.resolve(
        resolved.id,
        "alice",
        {
            "action": "approve",
            "values": {"legacy": True},
            "comment": "resolved before upgrade",
        },
    )
    assert finalized is True
    assert resolved_after is not None
    assert resolved_after.status.value == "resolved"

    manifest = {
        "version": version,
        "pending_id": pending.id,
        "quorum_id": quorum.id,
        "resolved_id": resolved.id,
        "legacy_event_counts": {
            pending.id: len(store.events(pending.id)),
            quorum.id: len(store.events(quorum.id)),
            resolved.id: len(store.events(resolved.id)),
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"LEGACY_{version.replace('.', '_')}_DB_SEEDED")
    print("pending_id=" + pending.id)
    print("quorum_id=" + quorum.id)
    print("resolved_id=" + resolved.id)


if __name__ == "__main__":
    main()
