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
    with tempfile.TemporaryDirectory(prefix="humanqueue-resume-recovery-") as temp:
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

        receiver_state = {"count": 0, "request_ids": []}
        lock = threading.Lock()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - stdlib API
                length = int(self.headers.get("content-length") or "0")
                body = self.rfile.read(length)
                payload = json.loads(body.decode("utf-8"))
                request_id = str(payload["request_id"])
                with lock:
                    receiver_state["count"] += 1
                    receiver_state["request_ids"].append(request_id)

                response = json.dumps({
                    "request_id": request_id,
                    "resumed": True,
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, *_args) -> None:
                return

        receiver = ThreadingHTTPServer(("127.0.0.1", receiver_port), Handler)
        receiver_thread = threading.Thread(target=receiver.serve_forever, daemon=True)
        receiver_thread.start()

        # Simulate the exact crash window: the decision transaction commits the
        # canonical resolved state and durable resume intent, then that process
        # disappears before a Gateway worker can claim/send the callback.
        resolve_code = rf'''
from app.models import AttentionRequestCreate, ResumeTarget
from app.store import Store
from humanqueue.config import db_path

store = Store(db_path())
item = store.create(
    AttentionRequestCreate(
        source="resume-recovery-e2e",
        source_ref="machine-op-1",
        title="Resume after crash?",
        summary="Decision commits before any Gateway worker exists.",
        kind="approval",
        resume=ResumeTarget(
            mode="webhook",
            url="http://127.0.0.1:{receiver_port}/resume",
            secret="resume-recovery-secret",
        ),
    )
)
resolved, finalized = store.resolve(
    item.id,
    "ci-human",
    {{"action": "approve", "values": {{"probe": "crash-before-send"}}}},
)
assert finalized is True
assert resolved is not None
row = store.resume_outbox(item.id)
assert row is not None, row
assert row["status"] == "pending", row
assert int(row["attempts"]) == 0, row
print(item.id)
'''
        resolved = subprocess.run(
            [sys.executable, "-c", resolve_code],
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        rid = resolved.stdout.strip().splitlines()[-1]
        if not rid.startswith("attn_"):
            raise RuntimeError(f"unexpected request id: {rid!r}")

        with lock:
            if receiver_state["count"] != 0:
                raise RuntimeError("resume callback fired before Gateway restart")

        gateway = subprocess.Popen(
            [sys.executable, "-m", "humanqueue", "gateway", "run"],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            wait_for_health(base_url)

            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                with lock:
                    count = int(receiver_state["count"])
                if count >= 1:
                    break
                time.sleep(0.05)

            time.sleep(0.5)
            with lock:
                count = int(receiver_state["count"])
                request_ids = list(receiver_state["request_ids"])

            if count != 1:
                raise RuntimeError(
                    f"recovered resume delivered {count} times; expected exactly 1"
                )
            if request_ids != [rid]:
                raise RuntimeError(
                    f"recovered resume identity drifted: {request_ids} != {[rid]}"
                )

            status, detail = request_json(
                "GET",
                base_url + f"/v1/requests/{rid}",
                token,
            )
            if status != 200:
                raise RuntimeError(detail)

            event_types = [event["type"] for event in detail.get("events", [])]
            for required in (
                "resolved",
                "resume_enqueued",
                "resume_delivery_claimed",
                "resume_confirmed",
            ):
                if required not in event_types:
                    raise RuntimeError(
                        f"missing durable resume recovery event {required}: {event_types}"
                    )

            check_code = rf'''
from app.store import Store
from humanqueue.config import db_path

store = Store(db_path())
row = store.resume_outbox("{rid}")
assert row is not None, row
assert row["status"] == "done", row
assert int(row["attempts"]) == 1, row
assert row["result"], row
print(row["status"])
print(row["attempts"])
'''
            checked = subprocess.run(
                [sys.executable, "-c", check_code],
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            lines = [line.strip() for line in checked.stdout.splitlines() if line.strip()]
            if lines[:2] != ["done", "1"]:
                raise RuntimeError(f"unexpected resume outbox state: {checked.stdout}")

            print("RESUME_CRASH_WINDOW_RECOVERY_OK")
            print(f"request_id={rid}")
            print("resume_deliveries=1")
            print("resume_outbox_status=done")
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
