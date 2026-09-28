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


def request_json(method: str, url: str, token: str | None = None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers, method=method)
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
    with tempfile.TemporaryDirectory(prefix="humanqueue-resume-uncertain-") as temp:
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

        receiver_state = {"count": 0, "ids": []}
        lock = threading.Lock()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("content-length") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                rid = str(payload["request_id"])
                with lock:
                    receiver_state["count"] += 1
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

        # This process performs the actual remote callback successfully, then
        # intentionally exits before completing the local resume_outbox row.
        # That simulates a crash after remote acceptance but before local commit.
        send_code = rf'''
import asyncio

from app.models import AttentionRequestCreate, ResumeTarget
from app.resume import resume
from app.store import Store
from humanqueue.config import db_path

store = Store(db_path())
item = store.create(
    AttentionRequestCreate(
        source="resume-uncertain-e2e",
        source_ref="machine-op-1",
        title="Do not duplicate after ambiguous crash",
        summary="Remote accepts, local completion record is lost.",
        kind="approval",
        resume=ResumeTarget(
            mode="webhook",
            url="http://127.0.0.1:{receiver_port}/resume",
            secret="resume-uncertain-secret",
        ),
    )
)
resolved, finalized = store.resolve(
    item.id,
    "ci-human",
    {{"action": "approve", "values": {{"probe": "remote-accepted"}}}},
)
assert finalized is True
assert resolved is not None
claimed = store.claim_resume_outbox(
    request_id=item.id,
    limit=1,
    lease_seconds=0,
)
assert claimed == [item.id], claimed
result = asyncio.run(resume(resolved, resolved.resolution or {{}}))
assert result["delivered"] is True, result
assert result["confirmed"] is True, result

# Intentionally DO NOT call complete_resume_outbox().
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
            if receiver_state["count"] != 1:
                raise RuntimeError(
                    f"pre-crash receiver count must be 1, got {receiver_state['count']}"
                )

        gateway = subprocess.Popen(
            [sys.executable, "-m", "humanqueue", "gateway", "run"],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            wait_for_health(base_url)

            # The startup worker should reconcile the expired processing lease
            # to uncertain, but it must never send a second callback.
            deadline = time.monotonic() + 5
            outbox = None
            while time.monotonic() < deadline:
                _, detail = request_json(
                    "GET",
                    base_url + f"/v1/requests/{rid}",
                    token,
                )
                outbox = detail.get("resume_outbox")
                if outbox and outbox.get("status") == "uncertain":
                    break
                time.sleep(0.05)

            if not outbox or outbox.get("status") != "uncertain":
                raise RuntimeError(f"abandoned delivery did not become uncertain: {outbox}")

            time.sleep(0.5)
            with lock:
                count = int(receiver_state["count"])
                ids = list(receiver_state["ids"])
            if count != 1:
                raise RuntimeError(
                    f"Gateway replayed ambiguous resume after restart: count={count}"
                )
            if ids != [rid]:
                raise RuntimeError(f"receiver identity drifted: {ids}")

            _, detail = request_json(
                "GET",
                base_url + f"/v1/requests/{rid}",
                token,
            )
            event_types = [event["type"] for event in detail.get("events", [])]
            if event_types.count("resume_delivery_claimed") != 1:
                raise RuntimeError(f"unexpected claim events: {event_types}")
            if event_types.count("resume_delivery_uncertain") != 1:
                raise RuntimeError(f"missing uncertainty audit: {event_types}")
            if "resume_confirmed" in event_types:
                raise RuntimeError(
                    "crashed sender manufactured local confirmation it never committed"
                )

            print("RESUME_AMBIGUOUS_CRASH_NO_REPLAY_OK")
            print(f"request_id={rid}")
            print("receiver_side_effect_count=1")
            print("resume_outbox_status=uncertain")
            print("automatic_retry=false")
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
