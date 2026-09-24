from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from .models import (
    AttentionRequest, AttentionRequestCreate, BudgetPolicy, RequestStatus,
    SurfaceMode,
)
from .scoring import choose_surface, priority_score


class Store:
    def __init__(self, path: str = "attention.db"):
        self.path = path
        self.lock = Lock()
        self._init()

    def _conn(self):
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS requests (
                  id TEXT PRIMARY KEY,
                  source TEXT NOT NULL,
                  source_ref TEXT NOT NULL,
                  idempotency_key TEXT,
                  payload TEXT NOT NULL,
                  status TEXT NOT NULL,
                  priority REAL NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  claimed_by TEXT,
                  resolved_by TEXT,
                  resolution TEXT,
                  surface_mode TEXT NOT NULL DEFAULT 'queue',
                  superseded_by TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_idem
                  ON requests(source, idempotency_key)
                  WHERE idempotency_key IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_queue
                  ON requests(status, surface_mode, priority DESC, created_at ASC);
                CREATE TABLE IF NOT EXISTS events (
                  seq INTEGER PRIMARY KEY AUTOINCREMENT,
                  request_id TEXT NOT NULL,
                  type TEXT NOT NULL,
                  actor TEXT,
                  data TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS votes (
                  request_id TEXT NOT NULL,
                  actor TEXT NOT NULL,
                  fingerprint TEXT NOT NULL,
                  resolution TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  PRIMARY KEY(request_id, actor)
                );
                CREATE TABLE IF NOT EXISTS budgets (
                  group_name TEXT PRIMARY KEY,
                  policy TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                """
            )
            # Forward-migrate older MVP databases.
            cols = {r[1] for r in c.execute("PRAGMA table_info(requests)").fetchall()}
            if "surface_mode" not in cols:
                c.execute("ALTER TABLE requests ADD COLUMN surface_mode TEXT NOT NULL DEFAULT 'queue'")
            if "superseded_by" not in cols:
                c.execute("ALTER TABLE requests ADD COLUMN superseded_by TEXT")

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _fingerprint(resolution: dict[str, Any]) -> str:
        canonical = json.dumps({"action": resolution.get("action"), "values": resolution.get("values", {})}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def _budget_policy(self, c: sqlite3.Connection, group: str) -> BudgetPolicy:
        row = c.execute("SELECT policy FROM budgets WHERE group_name=?", (group,)).fetchone()
        return BudgetPolicy.model_validate(json.loads(row["policy"])) if row else BudgetPolicy(group=group)

    def set_budget(self, policy: BudgetPolicy) -> BudgetPolicy:
        now = self._now().isoformat()
        with self.lock, self._conn() as c:
            c.execute(
                "INSERT INTO budgets(group_name,policy,updated_at) VALUES (?,?,?) ON CONFLICT(group_name) DO UPDATE SET policy=excluded.policy,updated_at=excluded.updated_at",
                (policy.group, json.dumps(policy.model_dump(mode="json")), now),
            )
        return policy

    def get_budget(self, group: str = "default") -> BudgetPolicy:
        with self._conn() as c:
            return self._budget_policy(c, group)

    def _interrupts_last_hour(self, c: sqlite3.Connection, group: str) -> int:
        since = (self._now() - timedelta(hours=1)).isoformat()
        rows = c.execute("SELECT payload FROM requests WHERE surface_mode=? AND created_at>=?", (SurfaceMode.interrupt.value, since)).fetchall()
        total = 0
        for r in rows:
            if json.loads(r["payload"]).get("attention_group", "default") == group:
                total += 1
        return total

    def _quorum_progress(self, c: sqlite3.Connection, rid: str, req: AttentionRequestCreate) -> dict[str, Any]:
        rows = c.execute("SELECT actor,fingerprint,resolution FROM votes WHERE request_id=? ORDER BY created_at", (rid,)).fetchall()
        counts = Counter(r["fingerprint"] for r in rows)
        leaders = counts.most_common()
        required = req.route.quorum if req.route.mode in ("quorum", "all_of") else 1
        return {
            "mode": req.route.mode,
            "required": required,
            "votes": len(rows),
            "leading_votes": leaders[0][1] if leaders else 0,
            "actors": [r["actor"] for r in rows],
            "conflicted": len(counts) > 1,
        }

    def _row_to_model(self, row: sqlite3.Row, c: sqlite3.Connection | None = None) -> AttentionRequest:
        payload = json.loads(row["payload"])
        req_create = AttentionRequestCreate.model_validate(payload)
        close_conn = False
        if c is None:
            c = self._conn()
            close_conn = True
        progress = self._quorum_progress(c, row["id"], req_create)
        model = AttentionRequest(
            **payload,
            id=row["id"], status=row["status"], priority=row["priority"],
            surface_mode=row["surface_mode"] or SurfaceMode.queue.value,
            created_at=row["created_at"], updated_at=row["updated_at"],
            claimed_by=row["claimed_by"], resolved_by=row["resolved_by"],
            resolution=json.loads(row["resolution"]) if row["resolution"] else None,
            superseded_by=row["superseded_by"], quorum_progress=progress,
        )
        if close_conn:
            c.close()
        return model

    def create(self, req: AttentionRequestCreate) -> AttentionRequest:
        now = self._now()
        rid = f"attn_{uuid.uuid4().hex[:16]}"
        priority = priority_score(req)
        payload = req.model_dump(mode="json")
        with self.lock, self._conn() as c:
            if req.idempotency_key:
                existing = c.execute("SELECT * FROM requests WHERE source=? AND idempotency_key=?", (req.source, req.idempotency_key)).fetchone()
                if existing:
                    return self._row_to_model(existing, c)

            budget = self._budget_policy(c, req.attention_group)
            surface = choose_surface(priority, req, budget, self._interrupts_last_hour(c, req.attention_group))
            c.execute(
                "INSERT INTO requests(id,source,source_ref,idempotency_key,payload,status,priority,created_at,updated_at,claimed_by,resolved_by,resolution,surface_mode,superseded_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rid, req.source, req.source_ref, req.idempotency_key, json.dumps(payload), RequestStatus.pending.value, priority, now.isoformat(), now.isoformat(), None, None, None, surface.value, None),
            )
            c.execute("INSERT INTO events(request_id,type,actor,data,created_at) VALUES (?,?,?,?,?)", (rid, "created", None, json.dumps({"priority": priority, "surface_mode": surface.value}), now.isoformat()))

            if req.supersession_key:
                rows = c.execute("SELECT * FROM requests WHERE id<>? AND source=? AND status IN (?,?)", (rid, req.source, RequestStatus.pending.value, RequestStatus.claimed.value)).fetchall()
                for old in rows:
                    old_payload = json.loads(old["payload"])
                    if old_payload.get("supersession_key") == req.supersession_key:
                        c.execute("UPDATE requests SET status=?,superseded_by=?,updated_at=? WHERE id=?", (RequestStatus.superseded.value, rid, now.isoformat(), old["id"]))
                        c.execute("INSERT INTO events(request_id,type,actor,data,created_at) VALUES (?,?,?,?,?)", (old["id"], "superseded", None, json.dumps({"by": rid}), now.isoformat()))

            row = c.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
        return self._row_to_model(row)

    def get(self, rid: str) -> AttentionRequest | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
            return self._row_to_model(row, c) if row else None

    def queue(self, status: str = "pending", limit: int = 100, include_deferred: bool = False) -> list[AttentionRequest]:
        with self._conn() as c:
            if include_deferred:
                rows = c.execute("SELECT * FROM requests WHERE status=? ORDER BY CASE surface_mode WHEN 'interrupt' THEN 0 WHEN 'queue' THEN 1 WHEN 'batch' THEN 2 ELSE 3 END, priority DESC, created_at ASC LIMIT ?", (status, limit)).fetchall()
            else:
                rows = c.execute("SELECT * FROM requests WHERE status=? AND surface_mode IN (?,?) ORDER BY CASE surface_mode WHEN 'interrupt' THEN 0 ELSE 1 END, priority DESC, created_at ASC LIMIT ?", (status, SurfaceMode.interrupt.value, SurfaceMode.queue.value, limit)).fetchall()
            return [self._row_to_model(r, c) for r in rows]

    def batches(self) -> list[dict[str, Any]]:
        groups: dict[str, list[AttentionRequest]] = {}
        with self._conn() as c:
            rows = c.execute("SELECT * FROM requests WHERE status=? AND surface_mode=? ORDER BY priority DESC", (RequestStatus.pending.value, SurfaceMode.batch.value)).fetchall()
            for row in rows:
                req = self._row_to_model(row, c)
                key = req.batch_key or f"{req.source}:{req.kind.value}"
                groups.setdefault(key, []).append(req)
        return [
            {"batch_key": key, "count": len(items), "max_priority": max(i.priority for i in items), "estimated_attention_seconds": sum(i.signals.human_effort_seconds for i in items), "items": items}
            for key, items in sorted(groups.items(), key=lambda kv: max(i.priority for i in kv[1]), reverse=True)
        ]

    def claim(self, rid: str, actor: str) -> AttentionRequest | None:
        now = self._now().isoformat()
        with self.lock, self._conn() as c:
            row = c.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
            if not row:
                return None
            if row["status"] not in (RequestStatus.pending.value, RequestStatus.claimed.value):
                return self._row_to_model(row, c)
            req = AttentionRequestCreate.model_validate(json.loads(row["payload"]))
            if req.route.actors and actor not in req.route.actors:
                raise PermissionError("actor is not eligible for this request")
            c.execute("UPDATE requests SET status=?,claimed_by=?,updated_at=? WHERE id=?", (RequestStatus.claimed.value, actor, now, rid))
            c.execute("INSERT INTO events(request_id,type,actor,data,created_at) VALUES (?,?,?,?,?)", (rid, "claimed", actor, "{}", now))
            row = c.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
            return self._row_to_model(row, c)

    def resolve(self, rid: str, actor: str, resolution: dict[str, Any]) -> tuple[AttentionRequest | None, bool]:
        now = self._now().isoformat()
        with self.lock, self._conn() as c:
            row = c.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
            if not row:
                return None, False
            if row["status"] in (RequestStatus.resolved.value, RequestStatus.cancelled.value, RequestStatus.expired.value, RequestStatus.superseded.value):
                return self._row_to_model(row, c), row["status"] == RequestStatus.resolved.value

            req = AttentionRequestCreate.model_validate(json.loads(row["payload"]))
            if req.route.actors and actor not in req.route.actors:
                raise PermissionError("actor is not eligible for this request")

            fingerprint = self._fingerprint(resolution)
            encoded = json.dumps(resolution)
            c.execute("INSERT INTO votes(request_id,actor,fingerprint,resolution,created_at) VALUES (?,?,?,?,?) ON CONFLICT(request_id,actor) DO UPDATE SET fingerprint=excluded.fingerprint,resolution=excluded.resolution,created_at=excluded.created_at", (rid, actor, fingerprint, encoded, now))
            c.execute("INSERT INTO events(request_id,type,actor,data,created_at) VALUES (?,?,?,?,?)", (rid, "vote", actor, encoded, now))

            votes = c.execute("SELECT actor,fingerprint,resolution FROM votes WHERE request_id=?", (rid,)).fetchall()
            counts = Counter(v["fingerprint"] for v in votes)
            required = 1
            if req.route.mode == "quorum":
                required = req.route.quorum
            elif req.route.mode == "all_of":
                required = len(req.route.actors) if req.route.actors else req.route.quorum
            winning_fp, winning_count = counts.most_common(1)[0]
            finalized = winning_count >= required

            if finalized:
                winning = next(json.loads(v["resolution"]) for v in votes if v["fingerprint"] == winning_fp)
                voters = [v["actor"] for v in votes if v["fingerprint"] == winning_fp]
                winning["quorum"] = {"required": required, "actors": voters, "votes": winning_count}
                final_encoded = json.dumps(winning)
                c.execute("UPDATE requests SET status=?,resolved_by=?,resolution=?,updated_at=? WHERE id=?", (RequestStatus.resolved.value, actor, final_encoded, now, rid))
                c.execute("INSERT INTO events(request_id,type,actor,data,created_at) VALUES (?,?,?,?,?)", (rid, "resolved", actor, final_encoded, now))
            else:
                c.execute("UPDATE requests SET status=?,updated_at=? WHERE id=?", (RequestStatus.claimed.value, now, rid))
                c.execute("INSERT INTO events(request_id,type,actor,data,created_at) VALUES (?,?,?,?,?)", (rid, "quorum_wait", actor, json.dumps({"required": required, "leading_votes": winning_count}), now))

            row = c.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
            return self._row_to_model(row, c), finalized

    def resolve_batch(self, batch_key: str, actor: str, action: str, comment: str | None = None) -> list[AttentionRequest]:
        items = [b for batch in self.batches() if batch["batch_key"] == batch_key for b in batch["items"]]
        out = []
        for item in items:
            req, _ = self.resolve(item.id, actor, {"action": action, "values": {}, "comment": comment})
            if req:
                out.append(req)
        return out

    def record_event(
        self,
        rid: str,
        event_type: str,
        *,
        actor: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Append an auditable event without mutating request lifecycle state."""
        now = self._now().isoformat()
        with self.lock, self._conn() as c:
            exists = c.execute("SELECT 1 FROM requests WHERE id=?", (rid,)).fetchone()
            if not exists:
                return
            c.execute(
                "INSERT INTO events(request_id,type,actor,data,created_at) VALUES (?,?,?,?,?)",
                (rid, event_type, actor, json.dumps(data or {}, default=str), now),
            )

    def latest_event_seq(self) -> int:
        with self._conn() as c:
            row = c.execute("SELECT COALESCE(MAX(seq),0) AS seq FROM events").fetchone()
            return int(row["seq"])

    def clear_all(self) -> None:
        """Developer/demo helper. Production deployments should manage retention explicitly."""
        with self.lock, self._conn() as c:
            c.execute("DELETE FROM votes")
            c.execute("DELETE FROM events")
            c.execute("DELETE FROM requests")

    def policy_sandbox(self, policy_key: str, proposed_action: str | None = None) -> dict[str, Any]:
        """Replay a proposed policy against historical human decisions without enabling it."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT payload,resolution FROM requests WHERE status=? AND resolution IS NOT NULL",
                (RequestStatus.resolved.value,),
            ).fetchall()
        samples: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for row in rows:
            payload = json.loads(row["payload"])
            if payload.get("policy_key") == policy_key:
                samples.append((payload, json.loads(row["resolution"])))
        if not samples:
            return {"policy_key": policy_key, "samples": 0, "ready": False}
        actions = [r.get("action") for _, r in samples]
        dominant, dominant_count = Counter(actions).most_common(1)[0]
        proposed = proposed_action or dominant
        matches = sum(1 for action in actions if action == proposed)
        conflicts = len(actions) - matches
        attention_seconds = sum(int(p.get("signals", {}).get("human_effort_seconds", 30)) for p, _ in samples)
        avg_risk = sum(float(p.get("signals", {}).get("risk_if_wrong", 0.5)) for p, _ in samples) / len(samples)
        return {
            "policy_key": policy_key,
            "samples": len(samples),
            "ready": True,
            "proposed_action": proposed,
            "dominant_action": dominant,
            "agreement": round(matches / len(samples), 3),
            "matching_human_decisions": matches,
            "conflicts": conflicts,
            "attention_seconds_replayed": attention_seconds,
            "avg_risk_if_wrong": round(avg_risk, 3),
            "mode": "shadow_only",
            "enabled": False,
        }

    def events(self, rid: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM events WHERE request_id=? ORDER BY seq", (rid,)).fetchall()
        return [{"seq": r["seq"], "type": r["type"], "actor": r["actor"], "data": json.loads(r["data"]), "created_at": r["created_at"]} for r in rows]


    def metrics(self) -> dict[str, Any]:
        now = self._now()
        day = (now - timedelta(hours=24)).isoformat()
        with self._conn() as c:
            status_rows = c.execute("SELECT status,COUNT(*) n FROM requests GROUP BY status").fetchall()
            surface_rows = c.execute("SELECT surface_mode,COUNT(*) n FROM requests WHERE status IN (?,?) GROUP BY surface_mode", (RequestStatus.pending.value, RequestStatus.claimed.value)).fetchall()
            recent = c.execute("SELECT payload,status,resolution,created_at,updated_at,surface_mode FROM requests WHERE created_at>=?", (day,)).fetchall()
        attention_seconds = 0
        resolved = 0
        batched = 0
        deferred = 0
        decision_seconds = []
        for r in recent:
            p = json.loads(r["payload"])
            if r["status"] == RequestStatus.resolved.value:
                resolved += 1
                attention_seconds += int(p.get("signals", {}).get("human_effort_seconds", 0))
                try:
                    created = datetime.fromisoformat(r["created_at"]); updated = datetime.fromisoformat(r["updated_at"])
                    decision_seconds.append(max(0, (updated-created).total_seconds()))
                except Exception:
                    pass
            if r["surface_mode"] == SurfaceMode.batch.value:
                batched += 1
            elif r["surface_mode"] == SurfaceMode.defer.value:
                deferred += 1
        return {
            "status": {r["status"]: r["n"] for r in status_rows},
            "surface": {r["surface_mode"]: r["n"] for r in surface_rows},
            "last_24h": {
                "created": len(recent), "resolved": resolved,
                "estimated_human_attention_seconds": attention_seconds,
                "batched_requests": batched, "deferred_requests": deferred,
                "interruptions_avoided": batched + deferred,
                "avg_time_to_resolution_seconds": round(sum(decision_seconds)/len(decision_seconds), 1) if decision_seconds else None,
            },
        }

    def frontier(self, min_samples: int = 5, min_agreement: float = 0.9) -> list[dict[str, Any]]:
        """Suggest low-risk classes that humans resolve consistently. Never auto-enables them."""
        with self._conn() as c:
            rows = c.execute("SELECT payload,resolution FROM requests WHERE status=? AND resolution IS NOT NULL", (RequestStatus.resolved.value,)).fetchall()
        groups: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
        for row in rows:
            p, r = json.loads(row["payload"]), json.loads(row["resolution"])
            key = p.get("policy_key")
            if key:
                groups.setdefault(key, []).append((p, r))
        suggestions = []
        for key, samples in groups.items():
            if len(samples) < min_samples:
                continue
            actions = [s[1].get("action") for s in samples]
            dominant, count = Counter(actions).most_common(1)[0]
            agreement = count / len(samples)
            avg_risk = sum(s[0].get("signals", {}).get("risk_if_wrong", 0.5) for s in samples) / len(samples)
            saved = sum(s[0].get("signals", {}).get("human_effort_seconds", 30) for s in samples)
            if agreement >= min_agreement and avg_risk <= 0.35:
                suggestions.append({
                    "policy_key": key, "samples": len(samples), "dominant_action": dominant,
                    "agreement": round(agreement, 3), "avg_risk_if_wrong": round(avg_risk, 3),
                    "observed_attention_seconds": saved,
                    "recommendation": "candidate_for_human_authored_policy",
                })
        return sorted(suggestions, key=lambda x: (x["agreement"], x["samples"]), reverse=True)
