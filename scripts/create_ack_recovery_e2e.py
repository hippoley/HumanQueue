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


def wait_for_health(url: str) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            status, body = request_json("GET", url + "/health")
            if status == 200 and body.get("ok") is True:
                return
        except Exception:
            pass
        time.sleep(0.05)
    raise RuntimeError("Gateway did not become healthy")


def terminate(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


class ProxyState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.human_posts = 0
        self.idempotency_keys: list[str] = []
        self.response_request_ids: list[str] = []
        self.created_flags: list[bool] = []

    def record_post(self, key: str) -> int:
        with self.lock:
            self.human_posts += 1
            self.idempotency_keys.append(key)
            return self.human_posts

    def record_response(self, request_id: str, created: bool) -> None:
        with self.lock:
            self.response_request_ids.append(request_id)
            self.created_flags.append(created)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="humanqueue-create-ack-") as temp:
        root = Path(temp)
        home = root / "state"
        marker = root / "continued.txt"
        gateway_port = free_port()
        proxy_port = free_port()
        gateway_url = f"http://127.0.0.1:{gateway_port}"
        proxy_url = f"http://127.0.0.1:{proxy_port}"

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

        gateway = subprocess.Popen(
            [sys.executable, "-m", "humanqueue", "gateway", "run"],
            cwd=root,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        wait_for_health(gateway_url)

        state = ProxyState()

        class Handler(BaseHTTPRequestHandler):
            def _forward(self):
                length = int(self.headers.get("content-length", "0"))
                body = self.rfile.read(length) if length else None
                headers = {}
                if self.headers.get("authorization"):
                    headers["Authorization"] = self.headers["authorization"]
                if body is not None:
                    headers["Content-Type"] = self.headers.get(
                        "content-type", "application/json"
                    )
                target = gateway_url + self.path
                req = urllib.request.Request(
                    target,
                    data=body,
                    headers=headers,
                    method=self.command,
                )
                try:
                    with urllib.request.urlopen(req, timeout=5) as response:
                        return (
                            response.status,
                            response.read(),
                            response.headers.get("content-type", "application/json"),
                        )
                except urllib.error.HTTPError as exc:
                    return (
                        exc.code,
                        exc.read(),
                        exc.headers.get("content-type", "application/json"),
                    )

            def do_GET(self):
                status, body, content_type = self._forward()
                self.send_response(status)
                self.send_header("content-type", content_type)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("content-length", "0"))
                raw = self.rfile.read(length)
                payload = json.loads(raw.decode("utf-8")) if raw else {}
                headers = {}
                if self.headers.get("authorization"):
                    headers["Authorization"] = self.headers["authorization"]
                headers["Content-Type"] = self.headers.get(
                    "content-type", "application/json"
                )

                attempt = 0
                if self.path == "/v1/human":
                    key = str(payload.get("idempotency_key") or "")
                    if not key:
                        raise RuntimeError("SDK create request had no idempotency key")
                    attempt = state.record_post(key)

                req = urllib.request.Request(
                    gateway_url + self.path,
                    data=raw,
                    headers=headers,
                    method="POST",
                )
                try:
                    with urllib.request.urlopen(req, timeout=5) as response:
                        status = response.status
                        response_body = response.read()
                        content_type = response.headers.get(
                            "content-type", "application/json"
                        )
                except urllib.error.HTTPError as exc:
                    status = exc.code
                    response_body = exc.read()
                    content_type = exc.headers.get(
                        "content-type", "application/json"
                    )

                if self.path == "/v1/human" and status == 201:
                    parsed = json.loads(response_body.decode("utf-8"))
                    state.record_response(
                        str(parsed["request"]["id"]),
                        bool(parsed.get("created")),
                    )

                    if attempt == 1:
                        # The Gateway has already committed and successfully
                        # produced the response. Drop it after the fact so the
                        # client experiences an ambiguous transport failure.
                        self.close_connection = True
                        return

                self.send_response(status)
                self.send_header("content-type", content_type)
                self.send_header("content-length", str(len(response_body)))
                self.end_headers()
                self.wfile.write(response_body)

            def log_message(self, format, *args):
                return

        proxy = ThreadingHTTPServer(("127.0.0.1", proxy_port), Handler)
        threading.Thread(target=proxy.serve_forever, daemon=True).start()

        child_env = env.copy()
        child_env["HUMAN_QUEUE_CREATE_ACK_MARKER"] = str(marker)
        child_env["HUMAN_QUEUE_PROXY_URL"] = proxy_url
        child_code = r'''
import json
import os
from pathlib import Path
from humanqueue import HumanBoundary

print("CREATE_ACK_ASK_STARTED", flush=True)
decision = HumanBoundary(
    base_url=os.environ["HUMAN_QUEUE_PROXY_URL"],
    timeout=1.0,
).ask(
    "human://approve",
    source="create-ack-e2e",
    ref="operation-1",
    title="Recover a lost create acknowledgement?",
    wait=True,
    wait_timeout=10,
    poll_interval=0.1,
)
print("CREATE_ACK_DECISION " + json.dumps(decision, sort_keys=True), flush=True)
assert decision["action"] == "approve", decision
Path(os.environ["HUMAN_QUEUE_CREATE_ACK_MARKER"]).write_text(
    "continued",
    encoding="utf-8",
)
print("CREATE_ACK_SIDE_EFFECT_EXECUTED True", flush=True)
'''
        child = subprocess.Popen(
            [sys.executable, "-c", child_code],
            cwd=root,
            env=child_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        try:
            deadline = time.monotonic() + 10
            item = None
            while time.monotonic() < deadline:
                _, queue = request_json(
                    "GET",
                    gateway_url + "/v1/queue?status=pending",
                    token,
                )
                matches = [
                    row
                    for row in queue.get("items", [])
                    if row.get("source") == "create-ack-e2e"
                ]
                if (
                    matches
                    and state.human_posts >= 2
                    and len(state.response_request_ids) >= 2
                    and len(state.created_flags) >= 2
                ):
                    item = matches[0]
                    break
                if child.poll() is not None:
                    output = child.stdout.read() if child.stdout else ""
                    raise RuntimeError(
                        "SDK failed before recovering the lost acknowledgement:\n"
                        + output
                    )
                time.sleep(0.05)

            if item is None:
                raise RuntimeError(
                    "SDK did not recover a canonical request after response loss"
                )

            rid = str(item["id"])
            if state.human_posts != 2:
                raise RuntimeError(
                    f"expected exactly two create attempts, got {state.human_posts}"
                )
            if len(set(state.idempotency_keys)) != 1:
                raise RuntimeError(
                    f"retry changed idempotency key: {state.idempotency_keys}"
                )
            key = state.idempotency_keys[0]
            if not key.startswith("humanq-client:"):
                raise RuntimeError(f"unexpected generated idempotency key: {key}")

            if state.response_request_ids != [rid, rid]:
                raise RuntimeError(
                    "Gateway did not return the same canonical request on retry: "
                    f"{state.response_request_ids} expected={rid}"
                )
            if state.created_flags != [True, False]:
                raise RuntimeError(
                    f"expected create then dedupe recovery, got {state.created_flags}"
                )

            _, detail = request_json(
                "GET",
                gateway_url + f"/v1/requests/{rid}",
                token,
            )
            event_types = [event["type"] for event in detail.get("events", [])]
            if event_types.count("created") != 1:
                raise RuntimeError(
                    f"lost acknowledgement created duplicate canonical events: {event_types}"
                )

            request_json(
                "POST",
                gateway_url + f"/v1/requests/{rid}/resolve",
                token,
                {
                    "actor": "create-ack-ci-human",
                    "action": "approve",
                    "values": {"recovered": True},
                },
            )

            output, _ = child.communicate(timeout=10)
            if child.returncode != 0:
                raise RuntimeError(
                    "SDK failed after recovering create acknowledgement:\n" + output
                )
            if not marker.exists():
                raise RuntimeError(
                    "SDK recovered the request but never continued after decision:\n"
                    + output
                )
            if "CREATE_ACK_SIDE_EFFECT_EXECUTED True" not in output:
                raise RuntimeError(
                    "missing downstream continuation proof:\n" + output
                )

            print("LOST_CREATE_ACK_RECOVERY_OK")
            print(f"request_id={rid}")
            print(f"idempotency_key={key}")
            print("create_attempts=2")
            print("created_flags=true,false")
            print("canonical_created_events=1")
            print("downstream_continued=true")
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            proxy.shutdown()
            proxy.server_close()
            terminate(gateway)


if __name__ == "__main__":
    main()
