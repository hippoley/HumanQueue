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
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            status, body = request_json("GET", base_url + "/health")
            if status == 200 and body.get("ok") is True:
                return
        except Exception:
            pass
        time.sleep(0.1)
    raise RuntimeError("multi-worker Gateway did not become healthy")


class Receiver:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.payloads: list[dict] = []

    def append(self, payload: dict) -> None:
        with self.lock:
            self.payloads.append(payload)

    def count(self) -> int:
        with self.lock:
            return len(self.payloads)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="humanqueue-outbox-multiworker-") as temp:
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

        config_path = home / "config.json"
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        cfg["channels"] = {
            "multiworker-outbox": {
                "type": "webhook",
                "enabled": True,
                "url": f"http://127.0.0.1:{receiver_port}/human",
                "secret": "multiworker-outbox-secret",
            }
        }
        config_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        token = str(cfg["token"])

        receiver = Receiver()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length", "0"))
                body = self.rfile.read(length)
                receiver.append(json.loads(body.decode("utf-8")))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, format, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", receiver_port), Handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        create_code = r'''
from app.models import AttentionRequestCreate
from app.store import Store
from humanqueue.config import db_path

store = Store(db_path())
item, created = store.create_with_status(
    AttentionRequestCreate(
        source="outbox-multiworker-e2e",
        source_ref="operation-1",
        title="Only one worker should publish this.",
        summary="Two Gateway workers will race the same durable outbox row.",
        kind="approval",
        idempotency_key="outbox-multiworker:operation-1",
    )
)
assert created is True
print(item.id)
'''
        created = subprocess.run(
            [sys.executable, "-c", create_code],
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        rid = created.stdout.strip().splitlines()[-1]
        if not rid.startswith("attn_"):
            raise RuntimeError(f"unexpected request id: {rid!r}")

        if receiver.count() != 0:
            raise RuntimeError("webhook fired before Gateway startup")

        gateway = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(gateway_port),
                "--workers",
                "2",
            ],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        try:
            wait_for_health(base_url)

            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and receiver.count() < 1:
                time.sleep(0.05)

            if receiver.count() != 1:
                logs = gateway.stdout.read() if gateway.poll() is not None and gateway.stdout else ""
                raise RuntimeError(
                    f"expected exactly one webhook delivery from two workers, "
                    f"got {receiver.count()} logs={logs}"
                )

            # Allow the second worker enough time to contend/re-scan.
            time.sleep(1.0)
            if receiver.count() != 1:
                raise RuntimeError(
                    f"second worker duplicated channel delivery: count={receiver.count()}"
                )

            status, detail = request_json("GET", base_url + f"/v1/requests/{rid}", token)
            if status != 200:
                raise RuntimeError(detail)
            events = detail.get("events", [])
            claimed = [e for e in events if e["type"] == "channel_publish_claimed"]
            delivered = [e for e in events if e["type"] == "channel_delivered"]
            completed = [e for e in events if e["type"] == "channel_publish_completed"]

            if len(claimed) != 1:
                raise RuntimeError(f"expected one outbox claim: {claimed}")
            if len(delivered) != 1:
                raise RuntimeError(f"expected one channel delivery: {delivered}")
            if len(completed) != 1:
                raise RuntimeError(f"expected one outbox completion: {completed}")

            check_code = rf'''
from app.store import Store
from humanqueue.config import db_path
row = Store(db_path()).channel_outbox("{rid}")
assert row is not None, row
assert row["status"] == "done", row
assert int(row["attempts"]) == 1, row
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
            values = [line.strip() for line in checked.stdout.splitlines() if line.strip()]
            if values[:2] != ["done", "1"]:
                raise RuntimeError(f"unexpected final outbox row: {checked.stdout}")

            print("MULTIWORKER_OUTBOX_SINGLE_LEASE_OK")
            print(f"request_id={rid}")
            print("webhook_deliveries=1")
            print("outbox_attempts=1")
            print("MULTIWORKER_OUTBOX_E2E_OK")
        finally:
            server.shutdown()
            server.server_close()
            if gateway.poll() is None:
                gateway.terminate()
                try:
                    gateway.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    gateway.kill()
                    gateway.wait(timeout=5)


if __name__ == "__main__":
    main()
