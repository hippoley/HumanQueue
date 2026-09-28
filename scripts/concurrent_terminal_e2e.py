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
from concurrent.futures import ThreadPoolExecutor
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
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        return exc.code, json.loads(body)


def wait_for_health(base_url: str) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            status, body = request_json("GET", base_url + "/health")
            if status == 200 and body.get("ok") is True:
                return
        except Exception:
            pass
        time.sleep(0.1)
    raise RuntimeError("Gateway did not become healthy")


def create_boundary(base_url: str, token: str, ref: str) -> str:
    status, body = request_json(
        "POST",
        base_url + "/v1/human",
        token,
        {
            "uri": "human://approve",
            "source": "concurrency-e2e",
            "ref": ref,
            "title": "Approve one terminal outcome?",
            "summary": "Exactly one resolver must win.",
            "resume": {"mode": "none"},
        },
    )
    if status != 201:
        raise RuntimeError((status, body))
    return str(body["request"]["id"])


def detail(base_url: str, token: str, rid: str) -> dict:
    status, body = request_json("GET", base_url + f"/v1/requests/{rid}", token)
    if status != 200:
        raise RuntimeError((status, body))
    return body


def race_two_humans(base_url: str, token: str) -> None:
    rid = create_boundary(base_url, token, "human-human")
    barrier = threading.Barrier(2)

    def approve(actor: str):
        barrier.wait(timeout=5)
        return request_json(
            "POST",
            base_url + f"/v1/requests/{rid}/resolve",
            token,
            {"actor": actor, "actor_kind": "human", "action": "approve"},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(approve, "alice")
        b = pool.submit(approve, "bob")
        results = [a.result(timeout=10), b.result(timeout=10)]

    codes = sorted(code for code, _ in results)
    if codes != [200, 409]:
        raise RuntimeError(f"expected one winner and one conflict, got {results}")

    body = detail(base_url, token, rid)
    events = body.get("events", [])
    resolved = [e for e in events if e["type"] == "resolved"]
    resume_events = [e for e in events if e["type"] in {"resume_not_applicable", "resume_delivered", "resume_confirmed"}]
    votes = [e for e in events if e["type"] == "vote"]

    if len(resolved) != 1:
        raise RuntimeError(f"expected one resolved event: {resolved}")
    if len(resume_events) != 1:
        raise RuntimeError(f"expected one resume attempt/result: {resume_events}")
    if len(votes) != 1:
        raise RuntimeError(f"losing resolver still created a vote: {votes}")

    print("CONCURRENT_HUMAN_RESOLVE_EXACTLY_ONCE_OK")
    print(f"request_id={rid}")
    print(f"winner={resolved[0].get('actor')}")


def race_human_vs_expiry(base_url: str, token: str) -> None:
    rid = create_boundary(base_url, token, "human-vs-expiry")
    barrier = threading.Barrier(2)

    def human():
        barrier.wait(timeout=5)
        return request_json(
            "POST",
            base_url + f"/v1/requests/{rid}/resolve",
            token,
            {"actor": "alice", "actor_kind": "human", "action": "approve"},
        )

    def expire():
        barrier.wait(timeout=5)
        return request_json(
            "POST",
            base_url + f"/v1/requests/{rid}/outcome",
            token,
            {
                "actor": "expiry-worker",
                "actor_kind": "system",
                "outcome": "expired",
                "reason": "race probe",
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(human)
        b = pool.submit(expire)
        results = [a.result(timeout=10), b.result(timeout=10)]

    codes = sorted(code for code, _ in results)
    if codes != [200, 409]:
        raise RuntimeError(f"expected one winner and one conflict, got {results}")

    body = detail(base_url, token, rid)
    events = body.get("events", [])
    terminal = [e for e in events if e["type"] in {"resolved", "machine_expired"}]
    resume_events = [e for e in events if e["type"] in {"resume_not_applicable", "resume_delivered", "resume_confirmed"}]

    if len(terminal) != 1:
        raise RuntimeError(f"expected exactly one terminal event: {terminal}")

    final_status = body["request"]["status"]
    if final_status == "resolved":
        if terminal[0]["type"] != "resolved":
            raise RuntimeError((final_status, terminal))
        if len(resume_events) != 1:
            raise RuntimeError(f"human winner should resume exactly once: {resume_events}")
    elif final_status == "expired":
        if terminal[0]["type"] != "machine_expired":
            raise RuntimeError((final_status, terminal))
        if resume_events:
            raise RuntimeError(f"machine expiry must never trigger resume: {resume_events}")
    else:
        raise RuntimeError(f"unexpected final status {final_status}")

    print("HUMAN_VS_MACHINE_TERMINAL_RACE_EXACTLY_ONCE_OK")
    print(f"request_id={rid}")
    print(f"final_status={final_status}")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="humanqueue-concurrency-") as temp:
        root = Path(temp)
        home = root / "state"
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
            race_two_humans(base_url, token)
            race_human_vs_expiry(base_url, token)
            print("CONCURRENT_TERMINAL_PACKAGED_E2E_OK")
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
