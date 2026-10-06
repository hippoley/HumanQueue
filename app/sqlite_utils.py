from __future__ import annotations

import hashlib
import os
import sqlite3
import uuid
from pathlib import Path


class ClosingConnection(sqlite3.Connection):
    """SQLite connection whose context manager also releases the OS handle.

    sqlite3.Connection.__exit__ commits/rolls back but intentionally does not
    close the connection. For short-lived per-operation connections that leaves
    cleanup to GC, which is especially visible on Windows where an open handle
    prevents removing/replacing the database file.
    """

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect(path: str | Path, *, check_same_thread: bool = False) -> ClosingConnection:
    conn = sqlite3.connect(
        str(path),
        check_same_thread=check_same_thread,
        factory=ClosingConnection,
    )
    conn.row_factory = sqlite3.Row
    return conn



def check_database(path: str | Path, *, full: bool = False) -> dict:
    """Run SQLite's own integrity checker and return a machine-readable result."""

    target = Path(path).expanduser()
    result = {
        "path": str(target),
        "exists": target.exists(),
        "ok": False,
        "check": "integrity_check" if full else "quick_check",
        "messages": [],
        "tables": [],
        "bytes": target.stat().st_size if target.exists() else 0,
    }
    if not target.exists():
        result["messages"] = ["database file does not exist"]
        return result

    try:
        with connect(target, check_same_thread=False) as conn:
            pragma = "PRAGMA integrity_check" if full else "PRAGMA quick_check"
            messages = [str(row[0]) for row in conn.execute(pragma).fetchall()]
            tables = [
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                    "ORDER BY name"
                ).fetchall()
            ]
        result["messages"] = messages
        result["tables"] = tables
        result["ok"] = messages == ["ok"]
    except sqlite3.DatabaseError as exc:
        result["messages"] = [f"{type(exc).__name__}: {exc}"]
        result["ok"] = False
    return result


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup_database(
    source: str | Path,
    destination: str | Path,
    *,
    overwrite: bool = False,
) -> dict:
    """Create an online-consistent SQLite backup and verify it before publishing.

    The snapshot is first written to a unique temporary file in the destination
    directory. Only a backup that passes PRAGMA quick_check is atomically moved
    into the requested destination.
    """

    src = Path(source).expanduser()
    dst = Path(destination).expanduser()

    if not src.exists():
        raise FileNotFoundError(f"source database does not exist: {src}")
    if src.resolve() == dst.resolve():
        raise ValueError("backup destination must differ from the active database")

    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(dst.parent, 0o700)
    except OSError:
        pass

    if dst.exists() and not overwrite:
        raise FileExistsError(f"backup already exists: {dst}")

    temp = dst.with_name(f".{dst.name}.tmp-{uuid.uuid4().hex}")
    try:
        with connect(src, check_same_thread=False) as source_conn:
            backup_conn = sqlite3.connect(str(temp))
            try:
                source_conn.backup(backup_conn)
                backup_conn.commit()
            finally:
                backup_conn.close()

        verified = check_database(temp)
        if not verified["ok"]:
            raise sqlite3.DatabaseError(
                "backup failed SQLite quick_check: "
                + "; ".join(verified.get("messages") or [])
            )

        # Re-check the destination just before publication so the default mode
        # cannot silently replace an operator's existing backup.
        if dst.exists() and not overwrite:
            raise FileExistsError(f"backup already exists: {dst}")

        os.replace(temp, dst)
        try:
            os.chmod(dst, 0o600)
        except OSError:
            pass

        return {
            "source": str(src),
            "path": str(dst),
            "ok": True,
            "check": verified["check"],
            "messages": verified["messages"],
            "tables": verified["tables"],
            "bytes": dst.stat().st_size,
            "sha256": _sha256_file(dst),
        }
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass
