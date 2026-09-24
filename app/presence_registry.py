from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from pydantic import BaseModel, Field


class PresenceUpdate(BaseModel):
    source_id: str
    provider: str
    account: str
    session_id: str
    state: str = "unknown"
    title: str | None = None
    workspace: str | None = None
    last_user: str | None = None
    last_agent: str | None = None
    current_action: str | None = None
    waiting_reason: str | None = None
    progress: float | None = Field(default=None, ge=0, le=1)
    native: dict[str, Any] = Field(default_factory=dict)


class PresenceRegistry:
    def __init__(self, path: str):
        self.path = path
        self.lock = Lock()
        self._init()

    def _conn(self):
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _init(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS presence_sources(
                  id TEXT PRIMARY KEY,
                  provider TEXT NOT NULL,
                  account TEXT NOT NULL,
                  mode TEXT NOT NULL,
                  endpoint TEXT,
                  enabled INTEGER NOT NULL DEFAULT 1,
                  config TEXT NOT NULL DEFAULT '{}',
                  updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS presence_sessions(
                  source_id TEXT NOT NULL,
                  provider TEXT NOT NULL,
                  account TEXT NOT NULL,
                  session_id TEXT NOT NULL,
                  state TEXT NOT NULL,
                  title TEXT,
                  workspace TEXT,
                  last_user TEXT,
                  last_agent TEXT,
                  current_action TEXT,
                  waiting_reason TEXT,
                  progress REAL,
                  native TEXT NOT NULL DEFAULT '{}',
                  updated_at TEXT NOT NULL,
                  PRIMARY KEY(source_id, session_id)
                );

                CREATE INDEX IF NOT EXISTS idx_presence_state
                  ON presence_sessions(state, updated_at DESC);
                """
            )

    def upsert_source(
        self,
        source_id: str,
        provider: str,
        account: str,
        mode: str,
        endpoint: str | None,
        config: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        now = self._now()
        cfg = json.dumps(config or {}, separators=(",", ":"))
        with self.lock, self._conn() as c:
            c.execute(
                """
                INSERT INTO presence_sources(id,provider,account,mode,endpoint,enabled,config,updated_at)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                  provider=excluded.provider,
                  account=excluded.account,
                  mode=excluded.mode,
                  endpoint=excluded.endpoint,
                  enabled=excluded.enabled,
                  config=excluded.config,
                  updated_at=excluded.updated_at
                """,
                (source_id, provider, account, mode, endpoint, int(enabled), cfg, now),
            )
        return self.get_source(source_id)

    def get_source(self, source_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM presence_sources WHERE id=?", (source_id,)).fetchone()
        if not row:
            return None
        out = dict(row)
        out["enabled"] = bool(out["enabled"])
        out["config"] = json.loads(out["config"] or "{}")
        return out

    def list_sources(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM presence_sources ORDER BY provider,account").fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["enabled"] = bool(item["enabled"])
            item["config"] = json.loads(item["config"] or "{}")
            out.append(item)
        return out

    def update(self, update: PresenceUpdate) -> dict[str, Any]:
        now = self._now()
        with self.lock, self._conn() as c:
            c.execute(
                """
                INSERT INTO presence_sessions(
                  source_id,provider,account,session_id,state,title,workspace,last_user,last_agent,
                  current_action,waiting_reason,progress,native,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source_id,session_id) DO UPDATE SET
                  provider=excluded.provider,
                  account=excluded.account,
                  state=excluded.state,
                  title=COALESCE(excluded.title,presence_sessions.title),
                  workspace=COALESCE(excluded.workspace,presence_sessions.workspace),
                  last_user=COALESCE(excluded.last_user,presence_sessions.last_user),
                  last_agent=COALESCE(excluded.last_agent,presence_sessions.last_agent),
                  current_action=COALESCE(excluded.current_action,presence_sessions.current_action),
                  waiting_reason=excluded.waiting_reason,
                  progress=excluded.progress,
                  native=excluded.native,
                  updated_at=excluded.updated_at
                """,
                (
                    update.source_id,update.provider,update.account,update.session_id,update.state,
                    update.title,update.workspace,update.last_user,update.last_agent,
                    update.current_action,update.waiting_reason,update.progress,
                    json.dumps(update.native, separators=(",", ":"), default=str),now,
                ),
            )
            row = c.execute(
                "SELECT * FROM presence_sessions WHERE source_id=? AND session_id=?",
                (update.source_id,update.session_id),
            ).fetchone()
        item = dict(row)
        item["native"] = json.loads(item["native"] or "{}")
        return item

    def sessions(self, *, state: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        with self._conn() as c:
            if state:
                rows = c.execute(
                    "SELECT * FROM presence_sessions WHERE state=? ORDER BY updated_at DESC LIMIT ?",
                    (state, limit),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM presence_sessions ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["native"] = json.loads(item["native"] or "{}")
            out.append(item)
        return out
