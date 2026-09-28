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
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request_json(method: str, url: str, token: str | None = None, payload: dict | None = None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=10) as response:
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
    with tempfile.TemporaryDirectory(prefix="humanqueue-multiworker-create-") as temp:
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

        cfg_path = home / "config.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["channels"] = {
            "multiworker-probe": {
                "type": "webhook",
                "enabled": True,
                "url": f"http://127.0.0.1:{receiver_port}/human",
                "secret": "multiworker-probe-secret",
            }
        }
        cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
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
            payload = {
                "uri": "human://approve",
                "source": "multiworker-retry-e2e",
                "ref": "operation-1",
                "title": "Create exactly once?",
                "idempotency_key": "multiworker:operation-1:approval",
            }
            barrier = threading.Barrier(2)

            def post():
                barrier.wait(timeout=5)
                return request_json("POST", base_url + "/v1/human", token, payload)

            with ThreadPoolExecutor(max_workers=2) as pool:
                a = pool.submit(post)
                b = pool.submit(post)
                results = [a.result(timeout=15), b.result(timeout=15)]

            if [status for status, _ in results] != [201, 201]:
                raise RuntimeError(f"unexpected HTTP results: {results}")

            ids = {body["request"]["id"] for _, body in results}
            if len(ids) != 1:
                raise RuntimeError(f"two workers created different requests: {results}")
            created_flags = sorted(body.get("created") for _, body in results)
            if created_flags != [False, True]:
                raise RuntimeError(f"expected one created and one reused result: {results}")

            rid = next(iter(ids))
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and receiver.count() < 1:
                time.sleep(0.05)
            if receiver.count() != 1:
                raise RuntimeError(f"expected one human-facing publish, got {receiver.count()}")

            status, detail = request_json("GET", base_url + f"/v1/requests/{rid}", token)
            if status != 200:
                raise RuntimeError(detail)
            events = detail.get("events", [])
            if len([e for e in events if e["type"] == "created"]) != 1:
                raise RuntimeError(f"expected one created event: {events}")
            if len([e for e in events if e["type"] == "channel_delivered"]) != 1:
                raise RuntimeError(f"expected one channel_delivered event: {events}")

            print("MULTIWORKER_IDEMPOTENT_CREATE_OK")
            print(f"request_id={rid}")
            print("created_flags=false,true")
            print("webhook_deliveries=1")
            print("MULTIWORKER_CREATE_SIDE_EFFECT_E2E_OK")
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
