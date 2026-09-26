from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
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
    with urllib.request.urlopen(req, timeout=3) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for_health(url: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            body = request_json("GET", url + "/health")
            if body.get("ok") is True:
                return
        except Exception as exc:  # noqa: BLE001 - smoke test reports last startup error
            last_error = exc
        time.sleep(0.1)
    raise RuntimeError(f"gateway did not become healthy: {last_error}")


def wait_for_request(url: str, token: str, child: subprocess.Popen[str], marker: Path, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = request_json("GET", url + "/v1/queue?status=pending", token)
        for item in body.get("items", []):
            if item.get("source") == "packaged-e2e" and item.get("source_ref") == "smoke-run-001":
                if child.poll() is not None:
                    output = child.stdout.read() if child.stdout else ""
                    raise RuntimeError(f"client exited before human decision:\n{output}")
                if marker.exists():
                    raise RuntimeError("side effect happened before human decision")
                return item
        time.sleep(0.1)
    raise RuntimeError("timed out waiting for packaged SDK request")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="humanqueue-first-run-") as temp:
        root = Path(temp)
        home = root / "state"
        marker = root / "side-effect.txt"
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"

        env = os.environ.copy()
        env["HUMAN_QUEUE_HOME"] = str(home)

        onboard = subprocess.run(
            [
                sys.executable,
                "-m",
                "humanqueue",
                "onboard",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--force",
            ],
            cwd=root,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        if "local gateway initialized" not in onboard.stdout:
            raise RuntimeError(f"unexpected onboard output:\n{onboard.stdout}")

        config = json.loads((home / "config.json").read_text(encoding="utf-8"))
        token = str(config["token"])
        if not token.startswith("hq_"):
            raise RuntimeError("onboard did not create a gateway token")

        gateway = subprocess.Popen(
            [sys.executable, "-m", "humanqueue", "gateway", "run"],
            cwd=root,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        child: subprocess.Popen[str] | None = None
        try:
            wait_for_health(base_url)

            child_env = env.copy()
            child_env["HUMAN_QUEUE_E2E_MARKER"] = str(marker)
            child_code = r'''
import json
import os
from pathlib import Path
from humanqueue import HumanBoundary

print("ASK_STARTED", flush=True)
decision = HumanBoundary().ask(
    "human://approve",
    source="packaged-e2e",
    ref="smoke-run-001",
    title="Allow packaged first-run smoke test to continue?",
    why_now="CI is proving the installed wheel blocks until a real human decision exists.",
    risk=0.9,
    seconds=2,
    wait=True,
    wait_timeout=15,
    poll_interval=0.1,
)
print("CLIENT_DECISION " + json.dumps(decision, sort_keys=True), flush=True)
assert decision["action"] == "approve", decision
assert decision["actor"] == "ci-human", decision
assert decision["values"] == {"verified": True}, decision
Path(os.environ["HUMAN_QUEUE_E2E_MARKER"]).write_text("approve", encoding="utf-8")
print("SIDE_EFFECT_EXECUTED True", flush=True)
'''
            child = subprocess.Popen(
                [sys.executable, "-c", child_code],
                cwd=root,
                env=child_env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

            item = wait_for_request(base_url, token, child, marker)
            request_id = str(item["id"])
            if item.get("status") != "pending":
                raise RuntimeError(f"request was not pending before decision: {item}")

            resolved = request_json(
                "POST",
                base_url + f"/v1/requests/{request_id}/resolve",
                token,
                {
                    "actor": "ci-human",
                    "action": "approve",
                    "values": {"verified": True},
                    "comment": "packaged first-run E2E",
                },
            )
            if resolved["request"]["id"] != request_id:
                raise RuntimeError("resolve response changed canonical request identity")
            if resolved["request"]["status"] != "resolved" or resolved["finalized"] is not True:
                raise RuntimeError(f"request did not finalize: {resolved}")

            output, _ = child.communicate(timeout=10)
            if child.returncode != 0:
                raise RuntimeError(f"blocked client failed after resolve:\n{output}")
            if not marker.exists() or marker.read_text(encoding="utf-8") != "approve":
                raise RuntimeError(f"side effect marker missing after decision:\n{output}")
            if "SIDE_EFFECT_EXECUTED True" not in output:
                raise RuntimeError(f"blocked client did not visibly continue:\n{output}")

            detail = request_json("GET", base_url + f"/v1/requests/{request_id}", token)
            if detail["request"]["id"] != request_id:
                raise RuntimeError("detail lookup changed canonical request identity")
            event_types = [event["type"] for event in detail.get("events", [])]
            for required in ("created", "resolved", "resume_not_applicable"):
                if required not in event_types:
                    raise RuntimeError(f"missing lifecycle event {required}: {event_types}")

            queue = request_json("GET", base_url + "/v1/queue?status=pending", token)
            if any(row.get("id") == request_id for row in queue.get("items", [])):
                raise RuntimeError("resolved request remained in pending queue")

            print("PACKAGED_FIRST_RUN_E2E_OK")
            print(f"request_id={request_id}")
            print("lifecycle=" + " -> ".join(event_types))
        finally:
            if child is not None and child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            if gateway.poll() is None:
                gateway.terminate()
                try:
                    gateway.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    gateway.kill()
                    gateway.wait(timeout=5)
            if gateway.returncode not in (0, -15, 143):
                logs = gateway.stdout.read() if gateway.stdout else ""
                raise RuntimeError(f"gateway exited unexpectedly ({gateway.returncode}):\n{logs}")


if __name__ == "__main__":
    main()
