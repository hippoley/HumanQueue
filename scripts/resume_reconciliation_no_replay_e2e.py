from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
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
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, headers=headers, data=data, method=method)
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
    with tempfile.TemporaryDirectory(prefix="humanqueue-resume-reconcile-v2-") as temp:
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

        receiver_state = {"callbacks": 0, "ids": []}
        lock = threading.Lock()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("content-length") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                rid = str(payload["request_id"])
                with lock:
                    receiver_state["callbacks"] += 1
                    receiver_state["ids"].append(rid)

                body = json.dumps({"request_id": rid, "resumed": True}).encode("utf-8")
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

        # Deliver successfully to the receiver, then deliberately lose the
        # sender-side completion commit. This is the exact ambiguous state that
        # must never be auto-replayed.
        send_code = rf"""
import asyncio

from app.models import AttentionRequestCreate, ResumeTarget
from app.resume import resume
from app.store import Store
from humanqueue.config import db_path

store = Store(db_path())
item = store.create(
    AttentionRequestCreate(
        source="resume-reconcile-v2-e2e",
        source_ref="machine-op-1",
        title="Reconcile without replay",
        summary="Remote executes; local completion commit is intentionally lost.",
        kind="approval",
        resume=ResumeTarget(
            mode="webhook",
            url="http://127.0.0.1:{receiver_port}/resume",
            secret="resume-reconcile-v2-secret",
        ),
    )
)
resolved, finalized = store.resolve(
    item.id,
    "ci-human",
    {{"action": "approve", "values": {{"probe": "reconcile-no-replay"}}}},
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

# Simulate crash before complete_resume_outbox().
row = store.resume_outbox(item.id)
assert row is not None
assert row["status"] == "processing", row
assert int(row["attempts"]) == 1, row
print(item.id)
"""
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

            with lock:
                callbacks_before = int(receiver_state["callbacks"])

            status, reconciled = request_json(
                "POST",
                base_url + f"/v1/requests/{rid}/resume-reconcile",
                token,
                {
                    "actor": "ci-operator",
                    "actor_kind": "human",
                    "outcome": "executed",
                    "reason": "receiver audit contains the canonical request id",
                    "evidence": {
                        "receiver_log_id": "receiver-log-1",
                        "request_id": rid,
                    },
                },
            )
            if status != 200:
                raise RuntimeError(reconciled)
            if reconciled.get("transport_attempted") is not False:
                raise RuntimeError(f"reconciliation attempted transport: {reconciled}")
            if (reconciled.get("resume_outbox") or {}).get("status") != "done":
                raise RuntimeError(f"reconciliation did not close outbox: {reconciled}")

            # Give the live Gateway worker enough time that an accidental
            # pending/retry transition would have emitted another callback.
            time.sleep(0.75)
            with lock:
                callbacks_after = int(receiver_state["callbacks"])
                ids = list(receiver_state["ids"])

            if callbacks_before != 1 or callbacks_after != 1:
                raise RuntimeError(
                    "evidence reconciliation replayed the external callback: "
                    f"before={callbacks_before} after={callbacks_after}"
                )
            if ids != [rid]:
                raise RuntimeError(f"receiver identity drifted: {ids}")

            _, final_detail = request_json(
                "GET",
                base_url + f"/v1/requests/{rid}",
                token,
            )
            outbox = final_detail.get("resume_outbox") or {}
            if outbox.get("status") != "done":
                raise RuntimeError(f"unexpected final outbox: {outbox}")
            if int(outbox.get("attempts") or 0) != 1:
                raise RuntimeError(f"reconciliation created a retry attempt: {outbox}")

            events = final_detail.get("events", [])
            event_types = [event["type"] for event in events]
            if event_types.count("resume_delivery_claimed") != 1:
                raise RuntimeError(f"unexpected transport attempts: {event_types}")
            if event_types.count("resume_delivery_uncertain") != 1:
                raise RuntimeError(f"uncertainty audit missing: {event_types}")
            if event_types.count("resume_reconciled_executed") != 1:
                raise RuntimeError(f"reconciliation audit missing: {event_types}")
            if "resume_confirmed" in event_types:
                raise RuntimeError(
                    "external evidence was misreported as an in-band resume confirmation"
                )

            print("RESUME_EVIDENCE_RECONCILIATION_NO_REPLAY_OK")
            print(f"request_id={rid}")
            print("receiver_callbacks=1")
            print("attempts=1")
            print("transport_attempted=false")
            print("final_outbox_status=done")
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
