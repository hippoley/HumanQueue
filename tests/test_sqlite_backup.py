from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.models import AttentionRequestCreate, RequestKind
from app.sqlite_utils import backup_database, check_database
from app.store import Store


def request(ref: str) -> AttentionRequestCreate:
    return AttentionRequestCreate(
        source="backup-test",
        source_ref=ref,
        title=f"Backup {ref}",
        summary="SQLite backup integrity test",
        kind=RequestKind.approval,
        idempotency_key=f"backup:{ref}",
    )


def count_rows(path: Path, table: str) -> int:
    with sqlite3.connect(path) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def test_verified_backup_is_independent_snapshot(tmp_path: Path):
    source = tmp_path / "source.db"
    backup = tmp_path / "backup.db"
    store = Store(str(source))

    first = store.create(request("one"))
    second = store.create(request("two"))
    store.resolve(
        second.id,
        "alice",
        {"action": "approve", "values": {"safe": True}},
    )

    result = backup_database(source, backup)

    assert result["ok"] is True
    assert result["messages"] == ["ok"]
    assert len(result["sha256"]) == 64
    assert backup.exists()
    assert check_database(backup)["ok"] is True

    before = count_rows(backup, "requests")
    assert before == 2

    # The backup is a real snapshot, not another handle onto live state.
    store.create(request("three"))
    assert count_rows(source, "requests") == 3
    assert count_rows(backup, "requests") == 2

    copied = Store(str(backup))
    assert copied.get(first.id) is not None
    copied_second = copied.get(second.id)
    assert copied_second is not None
    assert copied_second.status.value == "resolved"


def test_backup_refuses_same_path_and_existing_destination(tmp_path: Path):
    source = tmp_path / "source.db"
    destination = tmp_path / "existing.db"
    Store(str(source)).create(request("one"))
    destination.write_bytes(b"do-not-replace")

    with pytest.raises(ValueError, match="must differ"):
        backup_database(source, source)

    with pytest.raises(FileExistsError, match="already exists"):
        backup_database(source, destination)

    assert destination.read_bytes() == b"do-not-replace"


def test_force_backup_replaces_only_after_verified_snapshot(tmp_path: Path):
    source = tmp_path / "source.db"
    destination = tmp_path / "existing.db"
    Store(str(source)).create(request("one"))
    destination.write_bytes(b"old-backup")

    result = backup_database(source, destination, overwrite=True)

    assert result["ok"] is True
    assert check_database(destination)["ok"] is True
    assert destination.read_bytes() != b"old-backup"
    assert count_rows(destination, "requests") == 1


def test_check_database_reports_corruption_without_mutating_file(tmp_path: Path):
    broken = tmp_path / "broken.db"
    original = b"this is not a sqlite database"
    broken.write_bytes(original)

    result = check_database(broken)

    assert result["exists"] is True
    assert result["ok"] is False
    assert result["messages"]
    assert broken.read_bytes() == original


def test_failed_backup_never_publishes_partial_destination(tmp_path: Path):
    broken = tmp_path / "broken-source.db"
    destination = tmp_path / "backup.db"
    broken.write_bytes(b"not sqlite")

    with pytest.raises(sqlite3.DatabaseError):
        backup_database(broken, destination)

    assert not destination.exists()
    assert not list(tmp_path.glob(".backup.db.tmp-*"))
