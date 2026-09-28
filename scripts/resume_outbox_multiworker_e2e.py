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
    with tempfile.TemporaryDirectory(prefix="humanqueue-resume-multiworker-") as temp:
        root = Path(temp)
        home = root / "state"
        gateway_port_a = free_port()
        gateway_port_b = free_port()
        receiver_port = free_port()
        base_a = f"http://127.0.0.1:{gateway_port_a}"
        base_b = f"http://127.0.0.1:{gateway_port_b}"

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
                str(gateway_port_a),
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
        receiver_state = {"count": 0, "ids": []}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("content-length") or "0")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                rid = str(payload["request_id"])
                with lock:
                    receiver_state["count"] += 1
                    receiver_state["ids"].append(rid)

                # Widen the lease/claim race while the winning worker is in
                # outbound I/O. A losing Gateway must not claim the same row.
                time.sleep(0.5)

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

        create_code = rf'''
from app.models import AttentionRequestCreate, ResumeTarget
from app.store import Store
from humanqueue.config import db_path

store = Store(db_path())
item = store.create(
    AttentionRequestCreate(
        source="resume-multiworker-e2e",
        source_ref="machine-op-1",
        title="Only one Gateway may resume this action",
        summary="Two Gateway workers race one durable resume intent.",
        kind="approval",
        resume=ResumeTarget(
            mode="webhook",
            url="http://127.0.0.1:{receiver_port}/resume",
            secret="resume-multiworker-secret",
        ),
    )
)
resolved, finalized = store.resolve(
    item.id,
    "ci-human",
    {{"action": "approve", "values": {{"probe": "multiworker"}}}},
)
assert finalized is True
assert resolved is not None
row = store.resume_outbox(item.id)
assert row is not None, row
assert row["status"] == "pending", row
assert int(row["attempts"]) == 0, row
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

        command_a = [
            sys.executable, "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1", "--port", str(gateway_port_a),
            "--log-level", "warning",
        ]
        command_b = [
            sys.executable, "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1", "--port", str(gateway_port_b),
            "--log-level", "warning",
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
            while time.monotonic() < deadline:
                with lock:
                    count = int(receiver_state["count"])
                if count >= 1:
                    break
                time.sleep(0.05)

            if count < 1:
                raise RuntimeError("neither Gateway delivered the pending resume")

            # Give both workers several scan intervals after the first callback.
            time.sleep(1.5)

            with lock:
                count = int(receiver_state["count"])
                ids = list(receiver_state["ids"])
            if count != 1:
                logs_a = (
                    gateway_a.stdout.read()
                    if gateway_a.poll() is not None and gateway_a.stdout
                    else ""
                )
                logs_b = (
                    gateway_b.stdout.read()
                    if gateway_b.poll() is not None and gateway_b.stdout
                    else ""
                )
                raise RuntimeError(
                    "multi-worker resume delivered more than once: "
                    f"count={count} ids={ids} logs_a={logs_a} logs_b={logs_b}"
                )
            if ids != [rid]:
                raise RuntimeError(f"resume receiver identity drifted: {ids} != {[rid]}")

            status, detail = request_json(
                "GET",
                base_a + f"/v1/requests/{rid}",
                token,
            )
            if status != 200:
                raise RuntimeError(detail)

            outbox = detail.get("resume_outbox") or {}
            if outbox.get("status") != "done":
                raise RuntimeError(f"resume outbox did not finish: {outbox}")
            if int(outbox.get("attempts") or 0) != 1:
                raise RuntimeError(f"resume outbox was claimed more than once: {outbox}")

            event_types = [event["type"] for event in detail.get("events", [])]
            if event_types.count("resume_delivery_claimed") != 1:
                raise RuntimeError(
                    f"expected one resume claim, got: {event_types}"
                )
            if event_types.count("resume_confirmed") != 1:
                raise RuntimeError(
                    f"expected one resume confirmation, got: {event_types}"
                )
            if "resume_delivery_uncertain" in event_types:
                raise RuntimeError(
                    f"healthy multi-worker delivery became uncertain: {event_types}"
                )

            print("MULTI_WORKER_RESUME_LEASE_OK")
            print(f"request_id={rid}")
            print("gateway_workers=2")
            print("resume_deliveries=1")
            print("resume_attempts=1")
            print("resume_delivery_claimed=1")
            print("resume_confirmed=1")
        finally:
            receiver.shutdown()
            receiver.server_close()
            receiver_thread.join(timeout=5)
            terminate(gateway_a)
            terminate(gateway_b)


if __name__ == "__main__":
    main()
