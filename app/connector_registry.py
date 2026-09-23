from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from pydantic import BaseModel, Field


class ConnectorEventIn(BaseModel):
    provider: str
    event_name: str
    session_id: str
    turn_id: str | None = None
    cwd: str | None = None
    model: str | None = None
    transcript_path: str | None = None
    latest_user_prompt: str | None = None
    latest_assistant_message: str | None = None
    tool_name: str | None = None
    tool_use_id: str | None = None
    tool_input: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConnectorRegistry:
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

    def _init(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS connector_sessions (
                  provider TEXT NOT NULL,
                  session_id TEXT NOT NULL,
                  status TEXT NOT NULL,
                  cwd TEXT,
                  model TEXT,
                  transcript_path TEXT,
                  last_turn_id TEXT,
                  last_user_prompt TEXT,
                  last_assistant_message TEXT,
                  last_tool_name TEXT,
                  last_tool_use_id TEXT,
                  updated_at TEXT NOT NULL,
                  PRIMARY KEY(provider, session_id)
                );

                CREATE TABLE IF NOT EXISTS connector_events (
                  seq INTEGER PRIMARY KEY AUTOINCREMENT,
                  provider TEXT NOT NULL,
                  session_id TEXT NOT NULL,
                  event_name TEXT NOT NULL,
                  turn_id TEXT,
                  capsule TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_connector_events_session
                  ON connector_events(provider, session_id, seq DESC);
                """
            )

    def record(self, event: ConnectorEventIn) -> dict[str, Any]:
        now = self._now()
        status = "ended" if event.event_name.lower() in {"sessionend", "session_end"} else "active"
        capsule = {
            "provider": event.provider,
            "session_id": event.session_id,
            "turn_id": event.turn_id,
            "event_name": event.event_name,
            "cwd": event.cwd,
            "model": event.model,
            "transcript_path": event.transcript_path,
            "latest_user_prompt": event.latest_user_prompt,
            "latest_assistant_message": event.latest_assistant_message,
            "tool_name": event.tool_name,
            "tool_use_id": event.tool_use_id,
            "tool_input": event.tool_input,
            "metadata": event.metadata,
        }

        with self.lock, self._conn() as c:
            c.execute(
                """
                INSERT INTO connector_sessions(
                  provider,session_id,status,cwd,model,transcript_path,last_turn_id,
                  last_user_prompt,last_assistant_message,last_tool_name,last_tool_use_id,updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(provider,session_id) DO UPDATE SET
                  status=excluded.status,
                  cwd=COALESCE(excluded.cwd,connector_sessions.cwd),
                  model=COALESCE(excluded.model,connector_sessions.model),
                  transcript_path=COALESCE(excluded.transcript_path,connector_sessions.transcript_path),
                  last_turn_id=COALESCE(excluded.last_turn_id,connector_sessions.last_turn_id),
                  last_user_prompt=COALESCE(excluded.last_user_prompt,connector_sessions.last_user_prompt),
                  last_assistant_message=COALESCE(excluded.last_assistant_message,connector_sessions.last_assistant_message),
                  last_tool_name=COALESCE(excluded.last_tool_name,connector_sessions.last_tool_name),
                  last_tool_use_id=COALESCE(excluded.last_tool_use_id,connector_sessions.last_tool_use_id),
                  updated_at=excluded.updated_at
                """,
                (
                    event.provider,event.session_id,status,event.cwd,event.model,
                    event.transcript_path,event.turn_id,event.latest_user_prompt,
                    event.latest_assistant_message,event.tool_name,event.tool_use_id,now,
                ),
            )
            c.execute(
                """
                INSERT INTO connector_events(provider,session_id,event_name,turn_id,capsule,created_at)
                VALUES(?,?,?,?,?,?)
                """,
                (
                    event.provider,event.session_id,event.event_name,event.turn_id,
                    json.dumps(capsule, default=str),now,
                ),
            )
        return capsule

    def sessions(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM connector_sessions ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def session(self, provider: str, session_id: str, event_limit: int = 20) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM connector_sessions WHERE provider=? AND session_id=?",
                (provider,session_id),
            ).fetchone()
            if not row:
                return None
            events = c.execute(
                """
                SELECT seq,event_name,turn_id,capsule,created_at
                FROM connector_events
                WHERE provider=? AND session_id=?
                ORDER BY seq DESC LIMIT ?
                """,
                (provider,session_id,event_limit),
            ).fetchall()
        data = dict(row)
        data["events"] = [
            {
                "seq": e["seq"],
                "event_name": e["event_name"],
                "turn_id": e["turn_id"],
                "capsule": json.loads(e["capsule"]),
                "created_at": e["created_at"],
            }
            for e in events
        ]
        return data
