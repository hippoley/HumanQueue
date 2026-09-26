from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
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
    while time.monotonic() < deadline:
        try:
            if request_json("GET", url + "/health").get("ok") is True:
                return
        except Exception:
            pass
        time.sleep(0.1)
    raise RuntimeError("Gateway did not become healthy")


def wait_for_codex_request(url: str, token: str, session_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        queue = request_json("GET", url + "/v1/queue?status=pending", token)
        for item in queue.get("items", []):
            if item.get("source") != "codex":
                continue
            context = item.get("context") or {}
            handle = context.get("native_handle") or {}
            if handle.get("session_id") == session_id:
                return item
        time.sleep(0.1)
    raise RuntimeError(f"timed out waiting for Codex request {session_id}")


def run_hook(python: str, env: dict[str, str], event: dict) -> subprocess.Popen[str]:
    child = subprocess.Popen(
        [python, "-m", "humanqueue", "connector", "hook", "codex-permission"],
        env=env,
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert child.stdin is not None
    child.stdin.write(json.dumps(event) + "\n")
    child.stdin.flush()
    child.stdin.close()
    child.stdin = None
    return child


def prove_decision(base_url: str, token: str, env: dict[str, str], action: str, expected_behavior: str) -> str:
    session_id = f"codex-hook-e2e-{action}"
    event = {
        "agent_id": "agent-e2e",
        "agent_type": "main",
        "cwd": os.getcwd(),
        "hook_event_name": "PermissionRequest",
        "model": "gpt-test",
        "permission_mode": "default",
        "session_id": session_id,
        "tool_input": {
            "command": "git push origin main",
            "description": f"Codex hook E2E {action} probe",
        },
        "tool_name": "Bash",
        "transcript_path": None,
        "turn_id": f"turn-{action}",
    }

    child = run_hook(sys.executable, env, event)
    try:
        item = wait_for_codex_request(base_url, token, session_id)
        request_id = str(item["id"])
        if item.get("status") != "pending":
            raise RuntimeError(f"Codex hook request was not pending: {item}")

        context = item.get("context") or {}
        handle = context.get("native_handle") or {}
        if handle.get("provider") != "codex":
            raise RuntimeError(f"missing Codex provider handle: {handle}")
        if handle.get("session_id") != session_id:
            raise RuntimeError(f"session identity drifted: {handle}")
        if handle.get("turn_id") != f"turn-{action}":
            raise RuntimeError(f"turn identity drifted: {handle}")
        if handle.get("resume_kind") != "codex_permission_request":
            raise RuntimeError(f"wrong resume kind: {handle}")

        payload = {
            "actor": "ci-human",
            "action": action,
            "values": {"source": "codex-hook-e2e"},
        }
        if action == "reject":
            payload["comment"] = "Denied by Codex hook E2E"
        resolved = request_json(
            "POST",
            base_url + f"/v1/requests/{request_id}/resolve",
            token,
            payload,
        )
        if resolved["request"]["id"] != request_id:
            raise RuntimeError("canonical request id changed while resolving Codex hook")

        stdout, stderr = child.communicate(timeout=10)
        if child.returncode != 0:
            raise RuntimeError(f"Codex hook process failed: {stderr}\n{stdout}")

        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if not lines:
            raise RuntimeError("Codex hook produced no stdout")
        output = json.loads(lines[-1])

        specific = output.get("hookSpecificOutput") or {}
        if specific.get("hookEventName") != "PermissionRequest":
            raise RuntimeError(f"wrong hook event output: {output}")
        decision = specific.get("decision") or {}
        if decision.get("behavior") != expected_behavior:
            raise RuntimeError(f"wrong Codex decision output: {output}")
        if action == "reject" and "Denied by Codex hook E2E" not in str(decision.get("message")):
            raise RuntimeError(f"deny message was not preserved: {output}")

        detail = request_json("GET", base_url + f"/v1/requests/{request_id}", token)
        event_types = [row["type"] for row in detail.get("events", [])]
        for required in ("created", "vote", "resolved", "resume_not_applicable"):
            if required not in event_types:
                raise RuntimeError(f"missing {required} from Codex hook lifecycle: {event_types}")

        print(f"CODEX_HOOK_{expected_behavior.upper()}_OK request_id={request_id}")
        return request_id
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="humanqueue-codex-hook-") as temp:
        root = Path(temp)
        home = root / "state"
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"

        env = os.environ.copy()
        env["HUMAN_QUEUE_HOME"] = str(home)
        env["HUMAN_QUEUE_HOOK_WAIT_SECONDS"] = "15"

        subprocess.run(
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
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        token = json.loads((home / "config.json").read_text(encoding="utf-8"))["token"]

        gateway = subprocess.Popen(
            [sys.executable, "-m", "humanqueue", "gateway", "run"],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            wait_for_health(base_url)
            allow_id = prove_decision(base_url, token, env, "approve", "allow")
            deny_id = prove_decision(base_url, token, env, "reject", "deny")
            if allow_id == deny_id:
                raise RuntimeError("distinct Codex PermissionRequests reused the same canonical id")
            print("CODEX_PACKAGED_HOOK_E2E_OK")
        finally:
            if gateway.poll() is None:
                gateway.terminate()
                try:
                    gateway.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    gateway.kill()
                    gateway.wait(timeout=5)


if __name__ == "__main__":
    main()
