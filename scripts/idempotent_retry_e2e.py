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


def request_json(method: str, url: str, token: str | None = None, payload: dict | None = None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


class Receiver:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests: list[dict] = []

    def append(self, payload: dict) -> None:
        with self.lock:
            self.requests.append(payload)

    def count(self) -> int:
        with self.lock:
            return len(self.requests)


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
    with tempfile.TemporaryDirectory(prefix="humanqueue-retry-") as temp:
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
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["channels"] = {
            "retry-probe": {
                "type": "webhook",
                "enabled": True,
                "url": f"http://127.0.0.1:{receiver_port}/human",
                "secret": "retry-probe-secret",
            }
        }
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        token = config["token"]

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
            [sys.executable, "-m", "humanqueue", "gateway", "run"],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        try:
            wait_for_health(base_url)

            payload = {
                "uri": "human://approve",
                "source": "retry-e2e",
                "ref": "operation-1",
                "title": "Continue once?",
                "idempotency_key": "retry-e2e:operation-1:approval",
            }

            status1, first = request_json("POST", base_url + "/v1/human", token, payload)
            status2, second = request_json("POST", base_url + "/v1/human", token, payload)

            if (status1, status2) != (201, 201):
                raise RuntimeError((status1, first, status2, second))

            rid1 = str(first["request"]["id"])
            rid2 = str(second["request"]["id"])
            if rid1 != rid2:
                raise RuntimeError(f"idempotent retry created two requests: {rid1} != {rid2}")
            if first.get("created") is not True:
                raise RuntimeError(f"first create did not report created=true: {first}")
            if second.get("created") is not False:
                raise RuntimeError(f"retry did not report created=false: {second}")

            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and receiver.count() < 1:
                time.sleep(0.05)

            if receiver.count() != 1:
                raise RuntimeError(f"expected one webhook publish, got {receiver.count()}")

            status, detail = request_json("GET", base_url + f"/v1/requests/{rid1}", token)
            if status != 200:
                raise RuntimeError(detail)
            events = detail.get("events", [])
            created = [e for e in events if e["type"] == "created"]
            delivered = [e for e in events if e["type"] == "channel_delivered"]

            if len(created) != 1:
                raise RuntimeError(f"expected one created event: {created}")
            if len(delivered) != 1:
                raise RuntimeError(f"expected one channel delivery event: {delivered}")

            print("IDEMPOTENT_RETRY_SAME_REQUEST_OK")
            print(f"request_id={rid1}")
            print("first_created=true")
            print("retry_created=false")
            print("webhook_deliveries=1")
            print("IDEMPOTENT_CREATE_SIDE_EFFECT_E2E_OK")
        finally:
            server.shutdown()
            server.server_close()
            if gateway.poll() is None:
                gateway.terminate()
                try:
                    gateway.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    gateway.kill()
                    gateway.wait(timeout=5)


if __name__ == "__main__":
    main()
