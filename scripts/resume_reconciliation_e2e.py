from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request_json(
    method: str,
    url: str,
    token: str | None = None,
    payload: dict | None = None,
):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def wait_for_health(base_url: str) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            status, body = request_json("GET", base_url + "/health")
            if status == 200 and body.get("ok") is True:
                return
        except Exception:
            pass
        time.sleep(0.1)
    raise RuntimeError("Gateway did not become healthy")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="humanqueue-resume-reconcile-") as temp:
        root = Path(temp)
        home = root / "state"
        gateway_port = free_port()
        receiver_port = free_port()
        base_url = f"http://127.0.0.1:{gateway_port}"

        env = os.environ.copy()
        env["HUMAN_QUEUE_HOME"] = str(home)

        subprocess.run(
            [
                sys.executable,
                "-m",
                "humanqueue",
                "onboard",
                "--host",
                "127.0.0.1",
                "--port",
                str(gateway_port),
                "--force",
            ],
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        cfg = json.loads((home / "config.json").read_text(encoding="utf-8"))
        token = str(cfg["token"])

        lock = threading.Lock()
        receiver_state = {
            "callbacks": 0,
            "side_effects": 0,
            "seen": set(),
            "ids": [],
        }

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("content-length") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                rid = str(payload["request_id"])

                with lock:
                    receiver_state["callbacks"] += 1
                    receiver_state["ids"].append(rid)
                    if rid not in receiver_state["seen"]:
                        receiver_state["seen"].add(rid)
                        receiver_state["side_effects"] += 1

                body = json.dumps({
                    "request_id": rid,
                    "resumed": True,
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args) -> None:
                return

        receiver = ThreadingHTTPServer(("127.0.0.1", receiver_port), Handler)
        receiver_thread = threading.Thread(target=receiver.serve_forever, daemon=True)
        receiver_thread.start()

        # First attempt really reaches the receiver, but this short-lived sender
        # exits before recording local completion. Its expired lease later
        # becomes uncertain.
        send_code = rf'''
import asyncio

from app.models import AttentionRequestCreate, ResumeTarget
from app.resume import resume
from app.store import Store
from humanqueue.config import db_path

store = Store(db_path())
item = store.create(
    AttentionRequestCreate(
        source="resume-reconcile-e2e",
        source_ref="machine-op-1",
        title="Retry only with receiver dedup",
        summary="First callback executes remotely but local completion is lost.",
        kind="approval",
        resume=ResumeTarget(
            mode="webhook",
            url="http://127.0.0.1:{receiver_port}/resume",
            secret="resume-reconcile-secret",
        ),
    )
)
resolved, finalized = store.resolve(
    item.id,
    "ci-human",
    {{"action": "approve", "values": {{"probe": "reconcile"}}}},
)
assert finalized is True
assert resolved is not None
assert store.claim_resume_outbox(
    request_id=item.id,
    limit=1,
    lease_seconds=0,
) == [item.id]
result = asyncio.run(resume(resolved, resolved.resolution or {{}}))
assert result["confirmed"] is True, result

row = store.resume_outbox(item.id)
assert row is not None
assert row["status"] == "processing", row
assert int(row["attempts"]) == 1, row
print(item.id)
'''
        sent = subprocess.run(
            [sys.executable, "-c", send_code],
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        rid = sent.stdout.strip().splitlines()[-1]
        if not rid.startswith("attn_"):
            raise RuntimeError(f"unexpected request id: {rid!r}")

        with lock:
            if receiver_state["callbacks"] != 1:
                raise RuntimeError(f"first callback missing: {receiver_state}")
            if receiver_state["side_effects"] != 1:
                raise RuntimeError(f"first side effect missing: {receiver_state}")

        gateway = subprocess.Popen(
            [sys.executable, "-m", "humanqueue", "gateway", "run"],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            wait_for_health(base_url)

            deadline = time.monotonic() + 5
            detail = None
            while time.monotonic() < deadline:
                _, detail = request_json(
                    "GET",
                    base_url + f"/v1/requests/{rid}",
                    token,
                )
                outbox = detail.get("resume_outbox") or {}
                if outbox.get("status") == "uncertain":
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError(f"resume did not become uncertain: {detail}")

            # Unsafe retry must be rejected before any second transport attempt.
            try:
                request_json(
                    "POST",
                    base_url + f"/v1/requests/{rid}/resume-reconcile",
                    token,
                    {
                        "actor": "ci-operator",
                        "action": "retry",
                        "reason": "retry without dedup proof",
                        "receiver_dedup_confirmed": False,
                    },
                )
                raise RuntimeError("unsafe retry unexpectedly succeeded")
            except urllib.error.HTTPError as exc:
                if exc.code != 422:
                    raise

            with lock:
                if receiver_state["callbacks"] != 1:
                    raise RuntimeError(
                        "rejected unsafe retry still emitted a callback"
                    )

            # Operator explicitly attests receiver-side deduplication by the
            # canonical request id. This may create a second callback, but the
            # receiver must execute the machine side effect only once.
            status, reconciled = request_json(
                "POST",
                base_url + f"/v1/requests/{rid}/resume-reconcile",
                token,
                {
                    "actor": "ci-operator",
                    "action": "retry",
                    "reason": "receiver deduplicates by canonical request_id",
                    "receiver_dedup_confirmed": True,
                },
            )
            if status != 200:
                raise RuntimeError(reconciled)

            deadline = time.monotonic() + 8
            final_detail = None
            while time.monotonic() < deadline:
                _, final_detail = request_json(
                    "GET",
                    base_url + f"/v1/requests/{rid}",
                    token,
                )
                outbox = final_detail.get("resume_outbox") or {}
                if outbox.get("status") == "done":
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError(
                    f"operator-authorized retry never completed: {final_detail}"
                )

            with lock:
                callbacks = int(receiver_state["callbacks"])
                side_effects = int(receiver_state["side_effects"])
                ids = list(receiver_state["ids"])

            if callbacks != 2:
                raise RuntimeError(
                    f"expected original + explicit retry callbacks, got {callbacks}"
                )
            if side_effects != 1:
                raise RuntimeError(
                    f"receiver dedup failed; side effects={side_effects}"
                )
            if ids != [rid, rid]:
                raise RuntimeError(f"canonical request identity drifted: {ids}")

            outbox = final_detail["resume_outbox"]
            if int(outbox["attempts"]) != 2:
                raise RuntimeError(f"expected second attempt audit: {outbox}")

            events = final_detail.get("events", [])
            event_types = [e["type"] for e in events]
            if event_types.count("resume_reconciled_retry_authorized") != 1:
                raise RuntimeError(f"missing retry authorization audit: {event_types}")
            if event_types.count("resume_delivery_claimed") != 2:
                raise RuntimeError(f"expected two claimed attempts: {event_types}")
            if event_types.count("resume_confirmed") != 1:
                raise RuntimeError(
                    f"only the locally committed retry may confirm: {event_types}"
                )

            claim_attempts = [
                e["data"].get("attempt")
                for e in events
                if e["type"] == "resume_delivery_claimed"
            ]
            if claim_attempts != [1, 2]:
                raise RuntimeError(
                    f"resume attempt numbers are not auditable: {claim_attempts}"
                )

            print("RESUME_EXPLICIT_DEDUP_RETRY_OK")
            print(f"request_id={rid}")
            print("callbacks=2")
            print("receiver_side_effects=1")
            print("attempts=2")
            print("claim_attempts=1,2")
            print("final_status=done")
        finally:
            receiver.shutdown()
            receiver.server_close()
            receiver_thread.join(timeout=5)
            if gateway.poll() is None:
                gateway.terminate()
                try:
                    gateway.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    gateway.kill()
                    gateway.wait(timeout=5)


if __name__ == "__main__":
    main()
