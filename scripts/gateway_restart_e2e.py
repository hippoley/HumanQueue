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


def wait_for_pending(
    url: str,
    token: str,
    child: subprocess.Popen[str],
    *,
    source: str,
    timeout: float = 10.0,
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = request_json("GET", url + "/v1/queue?status=pending", token)
        for item in body.get("items", []):
            if item.get("source") == source:
                if child.poll() is not None:
                    output = child.stdout.read() if child.stdout else ""
                    raise RuntimeError(f"client exited before gateway fault injection:\n{output}")
                return item
        time.sleep(0.05)
    raise RuntimeError(f"timed out waiting for pending request from {source}")


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
        timeout_child: subprocess.Popen[str] | None = None
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

            item = wait_for_pending(
                base_url,
                token,
                child,
                source="gateway-restart-e2e",
            )
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

            timeout_code = r'''
import json
from humanqueue import HumanBoundary

print("TIMEOUT_ASK_STARTED", flush=True)
try:
    HumanBoundary(timeout=0.25).ask(
        "human://clarify",
        source="gateway-timeout-e2e",
        ref="timeout-run-001",
        title="Keep canonical boundary pending after caller timeout?",
        wait=True,
        wait_timeout=1.0,
        poll_interval=0.1,
    )
except TimeoutError as exc:
    print("WAIT_TIMEOUT_OK " + str(exc), flush=True)
else:
    raise AssertionError("wait unexpectedly returned without a human decision")
'''
            timeout_child = subprocess.Popen(
                [sys.executable, "-c", timeout_code],
                cwd=root,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            timeout_item = wait_for_pending(
                base_url,
                token,
                timeout_child,
                source="gateway-timeout-e2e",
            )
            timeout_request_id = str(timeout_item["id"])

            stop_gateway(gateway)
            print("GATEWAY_LONG_OUTAGE_INJECTED")
            time.sleep(1.4)

            timeout_output, _ = timeout_child.communicate(timeout=5)
            if timeout_child.returncode != 0:
                raise RuntimeError(
                    "wait_timeout probe failed unexpectedly:\n" + timeout_output
                )
            if "WAIT_TIMEOUT_OK" not in timeout_output:
                raise RuntimeError(
                    "caller did not honor wait_timeout during persistent outage:\n"
                    + timeout_output
                )
            if "last transport error" not in timeout_output:
                raise RuntimeError(
                    "timeout did not retain transport failure context:\n" + timeout_output
                )

            gateway = start_gateway(root, env)
            wait_for_health(base_url)

            timeout_detail = request_json(
                "GET",
                base_url + f"/v1/requests/{timeout_request_id}",
                token,
            )
            timeout_request = timeout_detail["request"]
            if timeout_request["status"] != "pending":
                raise RuntimeError(
                    "caller wait timeout mutated canonical boundary lifecycle: "
                    + json.dumps(timeout_request)
                )
            if timeout_request.get("resolution") is not None:
                raise RuntimeError(
                    "caller wait timeout fabricated a human resolution: "
                    + json.dumps(timeout_request)
                )

            request_json(
                "POST",
                base_url + f"/v1/requests/{timeout_request_id}/outcome",
                token,
                {
                    "actor": "restart-e2e-cleanup",
                    "actor_kind": "service",
                    "outcome": "cancelled",
                    "reason": "test cleanup after proving caller timeout semantics",
                },
            )
            print("PACKAGED_WAIT_TIMEOUT_PRESERVES_BOUNDARY_OK")
            print(f"timeout_request_id={timeout_request_id}")
        finally:
            if timeout_child is not None and timeout_child.poll() is None:
                timeout_child.kill()
                timeout_child.wait(timeout=5)
            if child is not None and child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            stop_gateway(gateway)


if __name__ == "__main__":
    main()
