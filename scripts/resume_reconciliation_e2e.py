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


def request_json(method: str, url: str, token: str | None = None, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for_health(base_url: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if request_json("GET", base_url + "/health").get("ok") is True:
                return
        except Exception:
            pass
        time.sleep(0.1)
    raise RuntimeError("Gateway did not become healthy")


def wait_for_outbox(db_path: Path, request_id: str, status: str, timeout: float = 10.0) -> dict:
    from app.store import Store

    store = Store(str(db_path))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = store.resume_outbox(request_id)
        if row and row.get("status") == status:
            return row
        time.sleep(0.1)
    raise RuntimeError(
        f"resume outbox {request_id} did not reach {status}: {store.resume_outbox(request_id)}"
    )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="humanqueue-resume-reconcile-") as temp:
        root = Path(temp)
        home = root / "state"
        gateway_port = free_port()
        receiver_port = free_port()
        base_url = f"http://127.0.0.1:{gateway_port}"

        state = {
            "executed_attempts": 0,
            "executed_side_effects": 0,
            "retry_attempts": 0,
            "retry_side_effects": 0,
            "retry_request_id": None,
        }
        lock = threading.Lock()

        class Receiver(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("content-length") or "0")
                body = self.rfile.read(length)
                payload = json.loads(body.decode("utf-8"))
                request_id = str(payload["request_id"])

                if self.path == "/executed":
                    with lock:
                        state["executed_attempts"] += 1
                        state["executed_side_effects"] += 1
                    # Side effect happened, ACK disappeared.
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    self.connection.close()
                    return

                if self.path == "/retry":
                    with lock:
                        state["retry_attempts"] += 1
                        attempt = int(state["retry_attempts"])
                        state["retry_request_id"] = request_id

                    if attempt == 1:
                        # No side effect happened, but the transport outcome is
                        # ambiguous to the sender. Operator verifies absence.
                        try:
                            self.connection.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                        self.connection.close()
                        return

                    with lock:
                        state["retry_side_effects"] += 1

                    encoded = json.dumps({
                        "request_id": request_id,
                        "resumed": True,
                    }).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                    return

                self.send_response(404)
                self.end_headers()

            def log_message(self, *_args) -> None:
                return

        receiver = ThreadingHTTPServer(("127.0.0.1", receiver_port), Receiver)
        thread = threading.Thread(target=receiver.serve_forever, daemon=True)
        thread.start()

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
        token = json.loads((home / "config.json").read_text(encoding="utf-8"))["token"]
        db = home / "human-queue.db"

        gateway = subprocess.Popen(
            [sys.executable, "-m", "humanqueue", "gateway", "run"],
            cwd=root,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        try:
            wait_for_health(base_url)

            # Case 1: receiver definitely executed before ACK loss.
            first = request_json(
                "POST",
                base_url + "/v1/human",
                token,
                {
                    "uri": "human://approve",
                    "source": "resume-reconcile-e2e",
                    "ref": "executed-case",
                    "title": "Executed case",
                    "resume": {
                        "mode": "webhook",
                        "url": f"http://127.0.0.1:{receiver_port}/executed",
                    },
                },
            )
            executed_id = str(first["request"]["id"])
            request_json(
                "POST",
                base_url + f"/v1/requests/{executed_id}/resolve",
                token,
                {
                    "actor": "ci-human",
                    "action": "approve",
                },
            )
            wait_for_outbox(db, executed_id, "uncertain")

            reconcile_executed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "humanqueue",
                    "resume",
                    "reconcile",
                    executed_id,
                    "--executed",
                    "--actor",
                    "operator:ci",
                    "--reason",
                    "receiver audit confirms canonical request executed once",
                    "--json",
                ],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            executed_result = json.loads(reconcile_executed.stdout)
            if executed_result["status"] != "done":
                raise RuntimeError(f"executed reconciliation did not close outbox: {executed_result}")

            time.sleep(0.8)
            with lock:
                if state["executed_attempts"] != 1 or state["executed_side_effects"] != 1:
                    raise RuntimeError(f"executed case replayed unexpectedly: {state}")

            # Case 2: operator verifies the first ambiguous attempt did NOT execute.
            second = request_json(
                "POST",
                base_url + "/v1/human",
                token,
                {
                    "uri": "human://approve",
                    "source": "resume-reconcile-e2e",
                    "ref": "not-executed-case",
                    "title": "Not executed case",
                    "resume": {
                        "mode": "webhook",
                        "url": f"http://127.0.0.1:{receiver_port}/retry",
                    },
                },
            )
            retry_id = str(second["request"]["id"])
            request_json(
                "POST",
                base_url + f"/v1/requests/{retry_id}/resolve",
                token,
                {
                    "actor": "ci-human",
                    "action": "approve",
                },
            )
            wait_for_outbox(db, retry_id, "uncertain")

            with lock:
                if state["retry_attempts"] != 1 or state["retry_side_effects"] != 0:
                    raise RuntimeError(f"first retry-case attempt violated premise: {state}")

            reconcile_retry = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "humanqueue",
                    "resume",
                    "reconcile",
                    retry_id,
                    "--not-executed",
                    "--actor",
                    "operator:ci",
                    "--reason",
                    "receiver ledger confirms canonical request absent",
                    "--json",
                ],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            retry_result = json.loads(reconcile_retry.stdout)
            if retry_result["status"] != "pending":
                raise RuntimeError(f"not-executed reconciliation did not requeue: {retry_result}")

            done = wait_for_outbox(db, retry_id, "done", timeout=10)
            if int(done["attempts"]) != 2:
                raise RuntimeError(f"manual retry did not produce exactly second attempt: {done}")

            with lock:
                if state["retry_attempts"] != 2:
                    raise RuntimeError(f"retry case expected exactly two sends: {state}")
                if state["retry_side_effects"] != 1:
                    raise RuntimeError(f"retry case side effect count wrong: {state}")
                if state["retry_request_id"] != retry_id:
                    raise RuntimeError(f"retry canonical identity drifted: {state}")

            from app.store import Store
            store = Store(str(db))
            executed_events = [e["type"] for e in store.events(executed_id)]
            retry_events = [e["type"] for e in store.events(retry_id)]

            if executed_events.count("resume_reconciled_executed") != 1:
                raise RuntimeError(f"missing executed reconciliation audit: {executed_events}")
            if retry_events.count("resume_retry_authorized") != 1:
                raise RuntimeError(f"missing retry authorization audit: {retry_events}")
            if retry_events.count("resume_confirmed") != 1:
                raise RuntimeError(f"manual retry never reached exact confirmation: {retry_events}")

            print("RESUME_RECONCILIATION_E2E_OK")
            print(f"executed_request_id={executed_id}")
            print("executed_attempts=1")
            print("executed_side_effects=1")
            print(f"retried_request_id={retry_id}")
            print("retry_attempts=2")
            print("retry_side_effects=1")
            print("retry_final=resume_confirmed")
        finally:
            receiver.shutdown()
            receiver.server_close()
            thread.join(timeout=5)
            if gateway.poll() is None:
                gateway.terminate()
                try:
                    gateway.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    gateway.kill()
                    gateway.wait(timeout=5)


if __name__ == "__main__":
    main()
