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
        self.create_posts = 0
        self.lookup_gets = 0
        self.keys: list[str] = []
        self.request_ids: list[str] = []
        self.created_flags: list[bool] = []

    def record_create(self, key: str, request_id: str, created: bool) -> int:
        with self.lock:
            self.create_posts += 1
            self.keys.append(key)
            self.request_ids.append(request_id)
            self.created_flags.append(created)
            return self.create_posts

    def record_lookup(self) -> None:
        with self.lock:
            self.lookup_gets += 1


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="humanqueue-create-ack-exhaustion-") as temp:
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
            cwd=root,
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
            def _forward(self, body: bytes | None = None):
                headers = {}
                if self.headers.get("authorization"):
                    headers["Authorization"] = self.headers["authorization"]
                if body is not None:
                    headers["Content-Type"] = self.headers.get(
                        "content-type", "application/json"
                    )
                req = urllib.request.Request(
                    gateway_url + self.path,
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
                if self.path.startswith("/v1/idempotency/lookup"):
                    state.record_lookup()
                status, body, content_type = self._forward()
                self.send_response(status)
                self.send_header("content-type", content_type)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("content-length", "0"))
                raw = self.rfile.read(length)
                status, response_body, content_type = self._forward(raw)

                if self.path == "/v1/human" and status == 201:
                    sent = json.loads(raw.decode("utf-8"))
                    returned = json.loads(response_body.decode("utf-8"))
                    key = str(sent.get("idempotency_key") or "")
                    if not key:
                        raise RuntimeError("SDK POST was missing idempotency key")
                    state.record_create(
                        key,
                        str(returned["request"]["id"]),
                        bool(returned.get("created")),
                    )

                    # Drop *every* successful create acknowledgement. The only
                    # path left for the SDK is idempotent read recovery.
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
        child_env["HUMAN_QUEUE_EXHAUSTION_MARKER"] = str(marker)
        child_env["HUMAN_QUEUE_PROXY_URL"] = proxy_url
        child_code = r'''
import json
import os
from pathlib import Path
from humanqueue import HumanBoundary

print("ACK_EXHAUSTION_ASK_STARTED", flush=True)
decision = HumanBoundary(
    base_url=os.environ["HUMAN_QUEUE_PROXY_URL"],
    timeout=1.0,
).ask(
    "human://approve",
    source="create-ack-exhaustion-e2e",
    ref="operation-1",
    title="Recover after every create acknowledgement is lost?",
    wait=True,
    wait_timeout=10,
    poll_interval=0.1,
)
print("ACK_EXHAUSTION_DECISION " + json.dumps(decision, sort_keys=True), flush=True)
assert decision["action"] == "approve", decision
Path(os.environ["HUMAN_QUEUE_EXHAUSTION_MARKER"]).write_text(
    "continued",
    encoding="utf-8",
)
print("ACK_EXHAUSTION_SIDE_EFFECT_EXECUTED True", flush=True)
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
            deadline = time.monotonic() + 12
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
                    if row.get("source") == "create-ack-exhaustion-e2e"
                ]
                if matches and state.create_posts == 3 and state.lookup_gets >= 1:
                    item = matches[0]
                    break
                if child.poll() is not None:
                    output = child.stdout.read() if child.stdout else ""
                    raise RuntimeError(
                        "SDK exited before idempotent lookup recovered the request:\n"
                        + output
                    )
                time.sleep(0.05)

            if item is None:
                raise RuntimeError(
                    "SDK never recovered after all create acknowledgements were dropped"
                )

            rid = str(item["id"])
            if state.create_posts != 3:
                raise RuntimeError(
                    f"expected three ambiguous create attempts, got {state.create_posts}"
                )
            if len(set(state.keys)) != 1:
                raise RuntimeError(
                    f"create retries changed idempotency key: {state.keys}"
                )
            key = state.keys[0]
            if not key.startswith("humanq-client:"):
                raise RuntimeError(f"unexpected generated key: {key}")
            if state.request_ids != [rid, rid, rid]:
                raise RuntimeError(
                    f"POST retries did not bind one request: {state.request_ids}"
                )
            if state.created_flags != [True, False, False]:
                raise RuntimeError(
                    f"unexpected create/dedupe sequence: {state.created_flags}"
                )
            if state.lookup_gets < 1:
                raise RuntimeError("SDK did not use idempotent lookup after POST exhaustion")

            _, detail = request_json(
                "GET",
                gateway_url + f"/v1/requests/{rid}",
                token,
            )
            events = detail.get("events", [])
            if sum(1 for e in events if e["type"] == "created") != 1:
                raise RuntimeError(f"duplicate canonical create events: {events}")

            request_json(
                "POST",
                gateway_url + f"/v1/requests/{rid}/resolve",
                token,
                {
                    "actor": "ack-exhaustion-ci-human",
                    "action": "approve",
                    "values": {"lookup_recovered": True},
                },
            )

            output, _ = child.communicate(timeout=10)
            if child.returncode != 0:
                raise RuntimeError(
                    "SDK failed after idempotent lookup recovery:\n" + output
                )
            if not marker.exists():
                raise RuntimeError(
                    "SDK recovered request but did not continue:\n" + output
                )

            print("CREATE_ACK_EXHAUSTION_LOOKUP_RECOVERY_OK")
            print(f"request_id={rid}")
            print(f"idempotency_key={key}")
            print("create_attempts=3")
            print("created_flags=true,false,false")
            print(f"lookup_gets={state.lookup_gets}")
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
