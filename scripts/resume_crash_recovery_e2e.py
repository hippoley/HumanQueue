from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.models import AttentionRequestCreate, RequestKind, ResumeTarget
from app.store import Store


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_health(url: str, timeout: float = 10.0) -> None:
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url + "/health", timeout=1) as response:
                if json.load(response).get("ok") is True:
                    return
        except Exception:
            pass
        time.sleep(0.05)
    raise RuntimeError("gateway did not become healthy")


def main() -> None:
    receipts: list[dict] = []
    executions: set[str] = set()
    lock = threading.Lock()

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("content-length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            request_id = str(payload["request_id"])
            with lock:
                receipts.append(
                    {
                        "request_id": request_id,
                        "idempotency_key": self.headers.get("Idempotency-Key"),
                        "payload": payload,
                    }
                )
                executions.add(request_id)

            body = json.dumps(
                {"request_id": request_id, "resumed": True}
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    receiver_port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", receiver_port), Receiver)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    with tempfile.TemporaryDirectory(prefix="humanqueue-resume-crash-") as temp:
        root = Path(temp)
        home = root / "state"
        gateway_port = free_port()
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

        config = json.loads((home / "config.json").read_text(encoding="utf-8"))
        db = str(config["db"])
        store = Store(db)
        item = store.create(
            AttentionRequestCreate(
                source="resume-crash-e2e",
                source_ref="crash-window-001",
                title="Recover a persisted decision after Gateway crash",
                summary="The decision commits before the resume transport runs.",
                kind=RequestKind.approval,
                resume=ResumeTarget(
                    mode="webhook",
                    url=f"http://127.0.0.1:{receiver_port}/resume",
                ),
            )
        )
        resolved, finalized = store.resolve(
            item.id,
            "crash-ci-human",
            {
                "action": "approve",
                "values": {"crash_recovery": True},
            },
        )
        if not finalized or resolved is None:
            raise RuntimeError("failed to persist the simulated human decision")

        # This is the crash window: the canonical decision is durable, but no
        # resume transport has run and therefore no resume_* evidence exists.
        event_types = [event["type"] for event in store.events(item.id)]
        if "resume_queued" not in event_types:
            raise RuntimeError(
                "resolved webhook request did not durably queue its resume obligation: "
                + json.dumps(event_types)
            )
        forbidden = {
            "resume_confirmed",
            "resume_delivered_unconfirmed",
            "resume_undeliverable",
        }
        if forbidden.intersection(event_types):
            raise RuntimeError(
                f"unexpected pre-crash resume attempt evidence: {event_types}"
            )
        print(f"CRASH_WINDOW_PERSISTED request_id={item.id}")

        gateway = subprocess.Popen(
            [sys.executable, "-m", "humanqueue", "gateway", "run"],
            cwd=root,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            wait_for_health(f"http://127.0.0.1:{gateway_port}")

            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with lock:
                    if receipts:
                        break
                time.sleep(0.05)

            with lock:
                seen = list(receipts)
                execution_count = len(executions)

            if not seen:
                raise RuntimeError(
                    "resolved request survived the crash, but restarted Gateway "
                    "never recovered its missing resume delivery"
                )
            if execution_count != 1:
                raise RuntimeError(
                    f"receiver executed {execution_count} logical requests; expected one"
                )
            receipt = seen[-1]
            if receipt["request_id"] != item.id:
                raise RuntimeError(f"resume recovered wrong request: {receipt}")
            if receipt["idempotency_key"] != item.id:
                raise RuntimeError(
                    "resume recovery did not expose request_id as the idempotency key"
                )

            restarted = Store(db)
            final_events = [event["type"] for event in restarted.events(item.id)]
            if "resume_confirmed" not in final_events:
                raise RuntimeError(
                    "receiver confirmed resume, but canonical evidence was not recorded: "
                    + json.dumps(final_events)
                )

            print("PACKAGED_RESUME_CRASH_RECOVERY_OK")
            print(f"request_id={item.id}")
            print("lifecycle=" + " -> ".join(final_events))
        finally:
            if gateway.poll() is None:
                gateway.terminate()
                try:
                    gateway.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    gateway.kill()
                    gateway.wait(timeout=5)

    server.shutdown()
    server.server_close()


if __name__ == "__main__":
    main()
