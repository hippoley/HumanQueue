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


def wait_for_health(base_url: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status, body = request_json("GET", base_url + "/health")
            if status == 200 and body.get("ok") is True:
                return
        except Exception:
            pass
        time.sleep(0.05)
    raise RuntimeError(f"Gateway did not become healthy: {base_url}")


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


def terminate(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="humanqueue-outbox-contention-") as temp:
        root = Path(temp)
        home = root / "state"
        gateway_port_a = free_port()
        gateway_port_b = free_port()
        receiver_port = free_port()
        base_a = f"http://127.0.0.1:{gateway_port_a}"
        base_b = f"http://127.0.0.1:{gateway_port_b}"

        env = os.environ.copy()
        env["HUMAN_QUEUE_HOME"] = str(home)

        # Onboard only to create shared state/token. The two Gateway processes
        # below bind their own ports directly but import the same DB/config.
        subprocess.run(
            [
                sys.executable,
                "-m",
                "humanqueue",
                "onboard",
                "--host",
                "127.0.0.1",
                "--port",
                str(gateway_port_a),
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
            "contention-probe": {
                "type": "webhook",
                "enabled": True,
                "url": f"http://127.0.0.1:{receiver_port}/human",
                "secret": "contention-probe-secret",
            }
        }
        config_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        token = str(cfg["token"])

        receiver = Receiver()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length", "0"))
                body = self.rfile.read(length)
                payload = json.loads(body.decode("utf-8"))
                receiver.append(payload)

                # Keep the winning worker inside the remote delivery window long
                # enough for the second Gateway to scan the same outbox row.
                time.sleep(0.4)

                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, format, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", receiver_port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()

        create_code = r'''
from app.models import AttentionRequestCreate
from app.store import Store
from humanqueue.config import db_path

store = Store(db_path())
item, created = store.create_with_status(
    AttentionRequestCreate(
        source="multi-worker-outbox-e2e",
        source_ref="operation-1",
        title="Only one worker may publish this boundary",
        summary="Two Gateways will race the same durable outbox row.",
        kind="approval",
        idempotency_key="multi-worker-outbox-e2e:operation-1",
    )
)
assert created is True
row = store.channel_outbox(item.id)
assert row is not None
assert row["status"] == "pending"
assert int(row["attempts"]) == 0
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
            raise RuntimeError("webhook fired before Gateway workers started")

        command_a = [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(gateway_port_a),
            "--log-level",
            "warning",
        ]
        command_b = [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(gateway_port_b),
            "--log-level",
            "warning",
        ]

        gateway_a = subprocess.Popen(
            command_a,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        gateway_b = subprocess.Popen(
            command_b,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        try:
            wait_for_health(base_a)
            wait_for_health(base_b)

            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and receiver.count() < 1:
                time.sleep(0.05)

            if receiver.count() < 1:
                raise RuntimeError("neither Gateway delivered the pending outbox request")

            # Give the losing worker ample time to retry its startup scan. A
            # second delivery here would prove the lease is not exclusive.
            time.sleep(1.2)

            if receiver.count() != 1:
                logs_a = gateway_a.stdout.read() if gateway_a.poll() is not None and gateway_a.stdout else ""
                logs_b = gateway_b.stdout.read() if gateway_b.poll() is not None and gateway_b.stdout else ""
                raise RuntimeError(
                    "multi-worker outbox published more than once: "
                    f"deliveries={receiver.count()} logs_a={logs_a} logs_b={logs_b}"
                )

            status, detail = request_json("GET", base_a + f"/v1/requests/{rid}", token)
            if status != 200:
                raise RuntimeError(detail)

            events = detail.get("events", [])
            event_types = [event["type"] for event in events]
            for event_type in (
                "channel_publish_claimed",
                "channel_delivered",
                "channel_publish_completed",
            ):
                count = event_types.count(event_type)
                if count != 1:
                    raise RuntimeError(
                        f"expected exactly one {event_type}, found {count}: {event_types}"
                    )

            check_code = rf'''
from app.store import Store
from humanqueue.config import db_path

store = Store(db_path())
row = store.channel_outbox("{rid}")
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
            check_lines = [line.strip() for line in checked.stdout.splitlines() if line.strip()]
            if check_lines[:2] != ["done", "1"]:
                raise RuntimeError(f"unexpected outbox state: {checked.stdout!r}")

            print("MULTI_WORKER_OUTBOX_LEASE_OK")
            print(f"request_id={rid}")
            print("gateway_workers=2")
            print("webhook_deliveries=1")
            print("outbox_attempts=1")
            print("channel_publish_claimed=1")
            print("channel_delivered=1")
            print("channel_publish_completed=1")
        finally:
            server.shutdown()
            server.server_close()
            terminate(gateway_a)
            terminate(gateway_b)


if __name__ == "__main__":
    main()
