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
    timeout: float = 5.0,
) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for_health(url: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if request_json("GET", url + "/health", timeout=1).get("ok") is True:
                return
        except Exception:
            pass
        time.sleep(0.1)
    raise RuntimeError("Gateway did not become healthy")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="humanqueue-resume-crash-gap-") as temp:
        root = Path(temp)
        state_home = root / "state"
        gateway_port = free_port()
        receiver_port = free_port()
        base_url = f"http://127.0.0.1:{gateway_port}"

        receiver_state = {"count": 0, "request_ids": []}
        receiver_lock = threading.Lock()

        class Receiver(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - stdlib API
                length = int(self.headers.get("content-length") or "0")
                self.rfile.read(length)
                with receiver_lock:
                    receiver_state["count"] += 1
                    receiver_state["request_ids"].append(
                        self.headers.get("x-attention-request-id")
                    )
                body = json.dumps({
                    "request_id": self.headers.get("x-attention-request-id"),
                    "resumed": True,
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args) -> None:
                return

        receiver = ThreadingHTTPServer(("127.0.0.1", receiver_port), Receiver)
        receiver_thread = threading.Thread(target=receiver.serve_forever, daemon=True)
        receiver_thread.start()

        env = os.environ.copy()
        env["HUMAN_QUEUE_HOME"] = str(state_home)

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
        token = json.loads(
            (state_home / "config.json").read_text(encoding="utf-8")
        )["token"]

        # Test-only ASGI wrapper: use the installed app, but crash exactly
        # when the HTTP layer tries to resume after Store.resolve committed.
        crash_module = root / "crash_gateway.py"
        crash_module.write_text(
            """
import os
import app.main as main

async def crash_after_resolution_commit(item, resolution):
    os._exit(91)

main._resume_and_record = crash_after_resolution_commit
app = main.app
""".lstrip(),
            encoding="utf-8",
        )

        crash_gateway = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "crash_gateway:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(gateway_port),
            ],
            cwd=root,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        normal_gateway: subprocess.Popen[str] | None = None
        try:
            wait_for_health(base_url)

            created = request_json(
                "POST",
                base_url + "/v1/human",
                token,
                {
                    "uri": "human://approve",
                    "source": "resume-crash-gap-e2e",
                    "ref": "machine-action-before-crash",
                    "title": "Resume after durable decision?",
                    "resume": {
                        "mode": "webhook",
                        "url": f"http://127.0.0.1:{receiver_port}/resume",
                        "secret": "crash-gap-secret",
                    },
                },
            )
            request_id = str(created["request"]["id"])

            resolve_payload = {
                "actor": "ci-human",
                "actor_kind": "human",
                "action": "approve",
                "values": {"probe": "crash-after-commit"},
            }
            try:
                request_json(
                    "POST",
                    base_url + f"/v1/requests/{request_id}/resolve",
                    token,
                    resolve_payload,
                    timeout=3,
                )
                raise RuntimeError("crash probe unexpectedly returned an HTTP response")
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
                pass

            crash_gateway.wait(timeout=5)
            if crash_gateway.returncode != 91:
                logs = crash_gateway.stdout.read() if crash_gateway.stdout else ""
                raise RuntimeError(
                    f"crash gateway did not exit at injected boundary: "
                    f"{crash_gateway.returncode}\n{logs}"
                )

            # At this point the human decision must already be durable.
            # Read SQLite through a fresh Store before starting any new Gateway.
            from app.store import Store

            store = Store(str(state_home / "human-queue.db"))
            durable = store.get(request_id)
            if durable is None or durable.status.value != "resolved":
                raise RuntimeError(
                    f"human decision was not durable before crash: {durable}"
                )

            with receiver_lock:
                before_restart = int(receiver_state["count"])
            if before_restart != 0:
                raise RuntimeError(
                    "resume callback happened before injected crash; probe boundary is wrong"
                )

            normal_gateway = subprocess.Popen(
                [sys.executable, "-m", "humanqueue", "gateway", "run"],
                cwd=root,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            wait_for_health(base_url)

            # Give restart recovery enough time to reveal itself.
            time.sleep(2.0)

            detail = request_json(
                "GET",
                base_url + f"/v1/requests/{request_id}",
                token,
            )
            with receiver_lock:
                after_restart = int(receiver_state["count"])

            resume_events = [
                event
                for event in detail.get("events", [])
                if event["type"].startswith("resume_")
            ]

            if detail["request"]["status"] != "resolved":
                raise RuntimeError(f"resolved decision changed after restart: {detail}")
            if after_restart != 0:
                raise RuntimeError(
                    f"current implementation unexpectedly recovered resume: "
                    f"receiver_count={after_restart}"
                )
            if resume_events:
                raise RuntimeError(
                    f"resume audit event appeared despite callback never running: {resume_events}"
                )

            print("RESUME_COMMIT_BEFORE_CALLBACK_GAP_REPRODUCED")
            print(f"request_id={request_id}")
            print("decision_status=resolved")
            print("receiver_count_after_restart=0")
            print("resume_events_after_restart=0")
        finally:
            receiver.shutdown()
            receiver.server_close()
            receiver_thread.join(timeout=5)

            if crash_gateway.poll() is None:
                crash_gateway.kill()
                crash_gateway.wait(timeout=5)
            if normal_gateway is not None and normal_gateway.poll() is None:
                normal_gateway.terminate()
                try:
                    normal_gateway.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    normal_gateway.kill()
                    normal_gateway.wait(timeout=5)


if __name__ == "__main__":
    main()
