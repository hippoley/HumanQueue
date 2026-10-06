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


def request_json(
    method: str,
    url: str,
    token: str | None = None,
    payload: dict | None = None,
) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for_health(url: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if request_json("GET", url + "/health").get("ok") is True:
                return
        except Exception:
            pass
        time.sleep(0.1)
    raise RuntimeError("Gateway did not become healthy")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="humanqueue-resume-ack-loss-") as temp:
        root = Path(temp)
        home = root / "state"
        gateway_port = free_port()
        receiver_port = free_port()
        base_url = f"http://127.0.0.1:{gateway_port}"

        state = {
            "count": 0,
            "request_ids": [],
            "bodies": [],
            "signatures": [],
        }
        lock = threading.Lock()

        class DropAckHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - stdlib API
                length = int(self.headers.get("content-length") or "0")
                body = self.rfile.read(length)
                with lock:
                    state["count"] += 1
                    state["request_ids"].append(
                        self.headers.get("x-attention-request-id")
                    )
                    state["signatures"].append(
                        self.headers.get("x-attention-signature")
                    )
                    state["bodies"].append(body.decode("utf-8"))

                # Simulate the hardest ambiguity: the target has already
                # executed the side effect, but the HTTP acknowledgement is
                # lost. Close the transport without returning a status line.
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.connection.close()

            def log_message(self, *_args) -> None:
                return

        receiver = ThreadingHTTPServer(
            ("127.0.0.1", receiver_port),
            DropAckHandler,
        )
        receiver_thread = threading.Thread(
            target=receiver.serve_forever,
            daemon=True,
        )
        receiver_thread.start()

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
        token = json.loads(
            (home / "config.json").read_text(encoding="utf-8")
        )["token"]

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

            created = request_json(
                "POST",
                base_url + "/v1/human",
                token,
                {
                    "uri": "human://approve",
                    "source": "resume-ack-loss-e2e",
                    "ref": "machine-action-001",
                    "title": "Execute exactly once?",
                    "resume": {
                        "mode": "webhook",
                        "url": f"http://127.0.0.1:{receiver_port}/resume",
                        "secret": "resume-ack-loss-secret",
                    },
                },
            )
            request_id = str(created["request"]["id"])

            resolved = request_json(
                "POST",
                base_url + f"/v1/requests/{request_id}/resolve",
                token,
                {
                    "actor": "ci-human",
                    "actor_kind": "human",
                    "action": "approve",
                    "values": {"probe": "ack-loss"},
                },
            )

            if resolved["request"]["status"] != "resolved":
                raise RuntimeError(f"boundary did not resolve: {resolved}")
            if resolved["resume"].get("delivered") is not False:
                raise RuntimeError(
                    f"dropped ACK must not be classified delivered: {resolved}"
                )
            if resolved["resume"].get("confirmed") is not False:
                raise RuntimeError(
                    f"dropped ACK must not be classified confirmed: {resolved}"
                )

            # Give any accidental retry enough time to become visible.
            time.sleep(1.0)

            with lock:
                count = int(state["count"])
                request_ids = list(state["request_ids"])
                bodies = list(state["bodies"])
                signatures = list(state["signatures"])

            if count != 1:
                raise RuntimeError(
                    f"ACK loss caused {count} resume deliveries; expected exactly 1"
                )
            if request_ids != [request_id]:
                raise RuntimeError(
                    f"resume transport identity drifted: {request_ids} != {request_id}"
                )
            if not signatures[0] or not signatures[0].startswith("sha256="):
                raise RuntimeError("signed resume lost its HMAC signature")

            decoded = json.loads(bodies[0])
            if decoded["request_id"] != request_id:
                raise RuntimeError("resume body did not bind canonical request id")
            if decoded["resolution"]["action"] != "approve":
                raise RuntimeError("resume body lost the canonical human decision")

            detail = request_json(
                "GET",
                base_url + f"/v1/requests/{request_id}",
                token,
            )
            resume_events = [
                event
                for event in detail.get("events", [])
                if event["type"].startswith("resume_")
            ]
            uncertain = [
                event
                for event in resume_events
                if event["type"] == "resume_delivery_uncertain"
            ]
            confirmed = [
                event
                for event in resume_events
                if event["type"] == "resume_confirmed"
            ]

            if len(uncertain) != 1:
                raise RuntimeError(
                    f"expected one resume_delivery_uncertain event: {resume_events}"
                )
            if uncertain[0]["data"].get("automatic_retry") is not False:
                raise RuntimeError(
                    f"ambiguous delivery must explicitly disable automatic retry: {uncertain}"
                )
            if confirmed:
                raise RuntimeError(
                    f"ACK loss manufactured resume confirmation: {confirmed}"
                )

            # Re-open the durable state directly: the target may already have
            # executed, so the outbox must stay fail-closed after ACK loss.
            from app.store import Store
            durable = Store(str(home / "human-queue.db"))
            outbox = durable.resume_outbox(request_id)
            if not outbox or outbox["status"] != "uncertain":
                raise RuntimeError(
                    f"ACK loss did not persist uncertain outbox state: {outbox}"
                )
            if int(outbox["attempts"]) != 1:
                raise RuntimeError(
                    f"ACK loss unexpectedly changed attempt count: {outbox}"
                )
            if durable.claim_resume_outbox(
                request_id=request_id,
                limit=1,
                lease_seconds=30,
            ):
                raise RuntimeError(
                    "ACK-loss resume was automatically replayable"
                )

            # Give any accidental retry another window to become visible.
            time.sleep(0.5)
            with lock:
                final_count = int(state["count"])
            if final_count != 1:
                raise RuntimeError(
                    f"ACK-loss state replayed the webhook {final_count} times"
                )

            print("RESUME_ACK_LOSS_UNCERTAIN_NO_RETRY_OK")
            print(f"request_id={request_id}")
            print("receiver_side_effect_count=1")
            print("outbox=uncertain")
            print("audit=resume_delivery_uncertain")
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
