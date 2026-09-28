from __future__ import annotations

import sqlite3
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
