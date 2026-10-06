from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import uuid
from pathlib import Path

from app.models import AttentionRequestCreate, RequestKind
from app.sqlite_utils import backup_database, check_database
from app.store import Store


def request(ref: str) -> AttentionRequestCreate:
    return AttentionRequestCreate(
        source="restore-probe",
        source_ref=ref,
        title=f"Restore probe {ref}",
        summary="Offline restore Reality Probe",
        kind=RequestKind.approval,
        idempotency_key=f"restore-probe:{ref}",
    )


def publish_verified_copy(source: Path, destination: Path) -> None:
    """Offline-only candidate publication used by the Reality Probe.

    The source must already be a standalone SQLite snapshot. Copy into a unique
    sibling temp file, verify that temp file, then atomically replace the target.
    """

    verified_source = check_database(source, full=True)
    if not verified_source["ok"]:
        raise RuntimeError(f"restore source failed integrity_check: {verified_source}")

    temp = destination.with_name(f".{destination.name}.restore-{uuid.uuid4().hex}")
    try:
        shutil.copy2(source, temp)
        verified_temp = check_database(temp, full=True)
        if not verified_temp["ok"]:
            raise RuntimeError(f"restore candidate failed integrity_check: {verified_temp}")
        os.replace(temp, destination)
    finally:
        if temp.exists():
            temp.unlink()


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="humanqueue-restore-probe-") as temp_dir:
        root = Path(temp_dir)
        active = root / "active.db"
        backup_a = root / "state-a.db"
        rollback_b = root / "rollback-state-b.db"

        store = Store(str(active))

        # State A: one pending boundary + one historical resolved boundary.
        pending_a = store.create(request("pending-a"))
        resolved_a = store.create(request("resolved-a"))
        resolved_after, finalized = store.resolve(
            resolved_a.id,
            "alice",
            {"action": "approve", "values": {"generation": "A"}},
            actor_kind="human",
        )
        assert finalized is True
        assert resolved_after is not None
        assert resolved_after.status.value == "resolved"

        events_a = {
            pending_a.id: len(store.events(pending_a.id)),
            resolved_a.id: len(store.events(resolved_a.id)),
        }

        backup = backup_database(active, backup_a)
        assert backup["ok"] is True
        assert check_database(backup_a, full=True)["ok"] is True

        # Mutate the active database into State B after the backup.
        pending_b = store.create(request("pending-b"))
        pending_a_after, finalized = store.resolve(
            pending_a.id,
            "bob",
            {"action": "reject", "values": {"generation": "B"}},
            actor_kind="human",
        )
        assert finalized is True
        assert pending_a_after is not None
        assert pending_a_after.status.value == "resolved"
        assert Store(str(active)).get(pending_b.id) is not None

        # The active DB is the operator's latest state. Snapshot it immediately
        # before any destructive restore so rollback remains possible.
        rollback = backup_database(active, rollback_b)
        assert rollback["ok"] is True
        assert check_database(rollback_b, full=True)["ok"] is True

        # All Store operations above use short-lived connections. At this point
        # the probe behaves like an offline restore: no Gateway or open Store
        # connection owns the active path.
        publish_verified_copy(backup_a, active)

        restored = Store(str(active))
        assert check_database(active, full=True)["ok"] is True

        restored_pending_a = restored.get(pending_a.id)
        restored_resolved_a = restored.get(resolved_a.id)
        restored_pending_b = restored.get(pending_b.id)

        assert restored_pending_a is not None
        assert restored_pending_a.status.value == "pending"
        assert restored_pending_a.resolution is None

        assert restored_resolved_a is not None
        assert restored_resolved_a.status.value == "resolved"
        assert restored_resolved_a.resolution is not None
        assert restored_resolved_a.resolution["action"] == "approve"
        assert restored_resolved_a.resolution["values"]["generation"] == "A"

        # State B was created after the snapshot and therefore must disappear.
        assert restored_pending_b is None

        # Restore publication itself must not rewrite historical request events.
        assert len(restored.events(pending_a.id)) == events_a[pending_a.id]
        assert len(restored.events(resolved_a.id)) == events_a[resolved_a.id]

        # Structural references in the restored DB must remain internally closed.
        with sqlite3.connect(active) as conn:
            dangling_events = conn.execute(
                """
                SELECT COUNT(*)
                FROM events e
                LEFT JOIN requests r ON r.id = e.request_id
                WHERE r.id IS NULL
                """
            ).fetchone()[0]
            dangling_votes = conn.execute(
                """
                SELECT COUNT(*)
                FROM votes v
                LEFT JOIN requests r ON r.id = v.request_id
                WHERE r.id IS NULL
                """
            ).fetchone()[0]
        assert dangling_events == 0
        assert dangling_votes == 0

        print("OFFLINE_RESTORE_TO_SNAPSHOT_OK")
        print("restored_pending=" + pending_a.id)
        print("post_snapshot_request_absent=" + pending_b.id)

        # Roll back the restore itself using the mandatory pre-restore snapshot.
        publish_verified_copy(rollback_b, active)

        rolled_back = Store(str(active))
        assert check_database(active, full=True)["ok"] is True
        rb_pending_a = rolled_back.get(pending_a.id)
        rb_pending_b = rolled_back.get(pending_b.id)
        assert rb_pending_a is not None
        assert rb_pending_a.status.value == "resolved"
        assert rb_pending_a.resolution is not None
        assert rb_pending_a.resolution["values"]["generation"] == "B"
        assert rb_pending_b is not None
        assert rb_pending_b.status.value == "pending"

        print("OFFLINE_RESTORE_ROLLBACK_OK")
        print("rollback_snapshot_sha256=" + rollback["sha256"])


if __name__ == "__main__":
    main()
