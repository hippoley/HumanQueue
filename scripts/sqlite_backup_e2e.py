from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from app.models import AttentionRequestCreate, RequestKind, ResumeTarget
from app.store import Store


def request(ref: str, *, webhook: bool = False) -> AttentionRequestCreate:
    return AttentionRequestCreate(
        source="backup-e2e",
        source_ref=ref,
        title=f"Backup E2E {ref}",
        summary="Concurrent-write online backup probe.",
        kind=RequestKind.approval,
        idempotency_key=f"backup-e2e:{ref}",
        resume=(
            ResumeTarget(
                mode="webhook",
                url="http://127.0.0.1:9999/resume",
                secret="backup-e2e-secret",
            )
            if webhook
            else ResumeTarget()
        ),
    )


def run_json(env: dict[str, str], *args: str) -> dict:
    completed = subprocess.run(
        [sys.executable, "-m", "humanqueue", *args],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="humanqueue-backup-e2e-") as temp:
        root = Path(temp)
        home = root / "state"
        backup = root / "snapshot.db"

        env = os.environ.copy()
        env["HUMAN_QUEUE_HOME"] = str(home)

        subprocess.run(
            [sys.executable, "-m", "humanqueue", "onboard", "--force"],
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        cfg = json.loads((home / "config.json").read_text(encoding="utf-8"))
        source = Path(cfg["db"])
        store = Store(str(source))

        for index in range(20):
            store.create(request(f"seed-{index}"))

        resumable = store.create(request("resumable", webhook=True))
        resolved, finalized = store.resolve(
            resumable.id,
            "alice",
            {"action": "approve", "values": {"safe": True}},
        )
        assert finalized is True
        assert resolved is not None
        assert store.resume_outbox(resumable.id)["status"] == "pending"

        writer_started = threading.Event()
        writer_done = threading.Event()

        def writer() -> None:
            worker = Store(str(source))
            for index in range(100):
                worker.create(request(f"live-{index}"))
                if index == 4:
                    writer_started.set()
                time.sleep(0.01)
            writer_done.set()

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        assert writer_started.wait(timeout=10)

        backup_result = run_json(env, "db", "backup", str(backup), "--json")
        assert backup_result["ok"] is True
        assert backup_result["messages"] == ["ok"]
        assert len(backup_result["sha256"]) == 64

        check_result = run_json(env, "db", "check", str(backup), "--json")
        assert check_result["ok"] is True
        assert check_result["messages"] == ["ok"]

        assert writer_done.wait(timeout=20)
        thread.join(timeout=2)

        source_conn = sqlite3.connect(source)
        try:
            source_count = int(
                source_conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
            )
        finally:
            source_conn.close()

        backup_conn = sqlite3.connect(backup)
        try:
            backup_count = int(
                backup_conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
            )
            dangling_events = int(
                backup_conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM events e
                    LEFT JOIN requests r ON r.id=e.request_id
                    WHERE r.id IS NULL
                    """
                ).fetchone()[0]
            )
            dangling_votes = int(
                backup_conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM votes v
                    LEFT JOIN requests r ON r.id=v.request_id
                    WHERE r.id IS NULL
                    """
                ).fetchone()[0]
            )
            dangling_channel = int(
                backup_conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM channel_outbox o
                    LEFT JOIN requests r ON r.id=o.request_id
                    WHERE r.id IS NULL
                    """
                ).fetchone()[0]
            )
            dangling_resume = int(
                backup_conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM resume_outbox o
                    LEFT JOIN requests r ON r.id=o.request_id
                    WHERE r.id IS NULL
                    """
                ).fetchone()[0]
            )
        finally:
            backup_conn.close()

        assert 26 <= backup_count <= source_count, (backup_count, source_count)
        assert source_count == 121
        assert dangling_events == 0
        assert dangling_votes == 0
        assert dangling_channel == 0
        assert dangling_resume == 0

        copied = Store(str(backup))
        copied_resumable = copied.get(resumable.id)
        assert copied_resumable is not None
        assert copied_resumable.status.value == "resolved"
        copied_resume = copied.resume_outbox(resumable.id)
        assert copied_resume is not None
        assert copied_resume["status"] == "pending"

        # Corruption must be reported as a failed check, not as a successful
        # empty/new SQLite database.
        broken = root / "broken.db"
        broken.write_bytes(b"not a sqlite database")
        failed = subprocess.run(
            [
                sys.executable,
                "-m",
                "humanqueue",
                "db",
                "check",
                str(broken),
                "--json",
            ],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert failed.returncode != 0
        broken_result = json.loads(failed.stdout)
        assert broken_result["ok"] is False

        print("SQLITE_ONLINE_BACKUP_E2E_OK")
        print(f"backup_requests={backup_count}")
        print(f"source_requests_after_writer={source_count}")
        print(f"sha256={backup_result['sha256']}")


if __name__ == "__main__":
    main()
