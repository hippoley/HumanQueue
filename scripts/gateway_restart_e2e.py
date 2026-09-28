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
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if request_json("GET", url + "/health").get("ok") is True:
                return
        except Exception as exc:
            last_error = exc
        time.sleep(0.05)
    raise RuntimeError(f"gateway did not become healthy: {last_error}")


def wait_for_pending(url: str, token: str, child: subprocess.Popen[str], timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = request_json("GET", url + "/v1/queue?status=pending", token)
        for item in body.get("items", []):
            if item.get("source") == "gateway-restart-e2e":
                if child.poll() is not None:
                    output = child.stdout.read() if child.stdout else ""
                    raise RuntimeError(f"client exited before gateway fault injection:\n{output}")
                return item
        time.sleep(0.05)
    raise RuntimeError("timed out waiting for pending restart-recovery request")


def start_gateway(root: Path, env: dict[str, str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-m", "humanqueue", "gateway", "run"],
        cwd=root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def stop_gateway(gateway: subprocess.Popen[str]) -> None:
    if gateway.poll() is not None:
        return
    gateway.terminate()
    try:
        gateway.wait(timeout=5)
    except subprocess.TimeoutExpired:
        gateway.kill()
        gateway.wait(timeout=5)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="humanqueue-restart-") as temp:
        root = Path(temp)
        home = root / "state"
        marker = root / "continued.txt"
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"

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
                str(port),
                "--force",
            ],
            cwd=root,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        config = json.loads((home / "config.json").read_text(encoding="utf-8"))
        token = str(config["token"])

        gateway = start_gateway(root, env)
        child: subprocess.Popen[str] | None = None
        try:
            wait_for_health(base_url)

            child_env = env.copy()
            child_env["HUMAN_QUEUE_RESTART_MARKER"] = str(marker)
            child_code = r'''
import json
import os
from pathlib import Path
from humanqueue import HumanBoundary

print("RESTART_ASK_STARTED", flush=True)
decision = HumanBoundary(timeout=0.4).ask(
    "human://approve",
    source="gateway-restart-e2e",
    ref="restart-run-001",
    title="Survive one local Gateway restart?",
    wait=True,
    wait_timeout=10,
    poll_interval=0.1,
)
print("RESTART_CLIENT_DECISION " + json.dumps(decision, sort_keys=True), flush=True)
assert decision["action"] == "approve", decision
Path(os.environ["HUMAN_QUEUE_RESTART_MARKER"]).write_text("continued", encoding="utf-8")
print("RESTART_SIDE_EFFECT_EXECUTED True", flush=True)
'''
            child = subprocess.Popen(
                [sys.executable, "-c", child_code],
                cwd=root,
                env=child_env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

            item = wait_for_pending(base_url, token, child)
            request_id = str(item["id"])
            print(f"RESTART_PENDING request_id={request_id}")

            # Fault injection: the canonical request already exists on disk, but
            # the waiting caller loses the Gateway for long enough to hit several polls.
            stop_gateway(gateway)
            print("GATEWAY_FAULT_INJECTED")
            time.sleep(0.8)

            if child.poll() is not None:
                output = child.stdout.read() if child.stdout else ""
                raise RuntimeError(
                    "blocked caller died during temporary Gateway outage:\n" + output
                )

            gateway = start_gateway(root, env)
            wait_for_health(base_url)
            print("GATEWAY_RESTARTED")

            detail = request_json("GET", base_url + f"/v1/requests/{request_id}", token)
            if detail["request"]["status"] != "pending":
                raise RuntimeError(f"request did not survive restart: {detail}")

            request_json(
                "POST",
                base_url + f"/v1/requests/{request_id}/resolve",
                token,
                {
                    "actor": "restart-ci-human",
                    "action": "approve",
                    "values": {"restart_recovered": True},
                },
            )

            output, _ = child.communicate(timeout=10)
            if child.returncode != 0:
                raise RuntimeError(f"client failed after Gateway recovery:\n{output}")
            if not marker.exists():
                raise RuntimeError(f"client never continued after recovery:\n{output}")
            if "RESTART_SIDE_EFFECT_EXECUTED True" not in output:
                raise RuntimeError(f"missing recovery side-effect proof:\n{output}")

            print("PACKAGED_GATEWAY_RESTART_RECOVERY_OK")
            print(f"request_id={request_id}")
        finally:
            if child is not None and child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            stop_gateway(gateway)


if __name__ == "__main__":
    main()
