from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path


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
        except Exception as exc:
            last_error = exc
        time.sleep(0.1)
    raise RuntimeError(f"human:// Gateway did not become healthy: {last_error}")


def run_json(args: list[str], *, cwd: Path) -> dict:
    completed = subprocess.run(
        args,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(args)}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    try:
        return json.loads(completed.stdout)
    except Exception as exc:
        raise RuntimeError(
            f"command did not return JSON: {' '.join(args)}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        ) from exc


def wait_for_opencode_boundary(
    base_url: str,
    token: str,
    *,
    session_id: str,
    marker: Path,
    shell_call: subprocess.Popen[str],
    timeout: float = 15.0,
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = request_json("GET", base_url + "/v1/queue?status=pending", token)
        for item in body.get("items", []):
            if item.get("source") != "opencode":
                continue
            context = item.get("context") or {}
            native = context.get("native_handle") or {}
            if str(native.get("session_id") or "") != session_id:
                continue
            if shell_call.poll() is not None:
                output = shell_call.stdout.read() if shell_call.stdout else ""
                raise RuntimeError(
                    "OpenCode shell call exited before the human decision:\n" + output
                )
            if marker.exists():
                raise RuntimeError("OpenCode shell side effect happened before human approval")
            return item
        time.sleep(0.1)
    raise RuntimeError("timed out waiting for native OpenCode permission boundary")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="humanq-opencode-native-") as temp:
        root = Path(temp)
        workspace = root / "workspace"
        workspace.mkdir()
        marker = workspace / "native-approved.txt"

        # V2 permission rules: force the direct shell API to ask.
        (workspace / "opencode.jsonc").write_text(
            json.dumps(
                {
                    "$schema": "https://opencode.ai/config.json",
                    "permissions": [
                        {"action": "shell", "resource": "*", "effect": "ask"},
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        config = json.loads(
            (Path.home() / ".human-queue" / "config.json").read_text(encoding="utf-8")
        )
        token = str(config["token"])
        port = int(config.get("port") or 7482)
        base_url = f"http://127.0.0.1:{port}"

        gateway = subprocess.Popen(
            ["humanq", "gateway", "run"],
            cwd=workspace,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        shell_call: subprocess.Popen[str] | None = None

        try:
            wait_for_health(base_url)

            # Ensure the real OpenCode service is alive for this location.
            service = subprocess.run(
                ["opencode", "service", "start"],
                cwd=workspace,
                text=True,
                capture_output=True,
                check=False,
                timeout=20,
            )
            if service.returncode != 0:
                raise RuntimeError(
                    f"OpenCode service failed to start:\n{service.stdout}\n{service.stderr}"
                )

            # Plugin discovery is Location-aware in the shared OpenCode service.
            # The preceding CI step already proves the real runtime loads the global
            # human:// plugin. In a brand-new temporary Location, the useful test is
            # whether a permission evaluation actually reaches human://, not whether
            # plugin-list has projected the Location before a session exists.
            plugins_before = subprocess.run(
                ["opencode", "plugin", "list"],
                cwd=workspace,
                text=True,
                capture_output=True,
                check=False,
                timeout=20,
            )
            print("plugins_before_session=" + plugins_before.stdout.strip(), flush=True)

            session = run_json(
                [
                    "opencode",
                    "api",
                    "v2.session.create",
                    "--data",
                    json.dumps({"title": "human:// native permission probe"}),
                ],
                cwd=workspace,
            )
            session_id = str(
                session.get("id")
                or (session.get("data") or {}).get("id")
                or (session.get("session") or {}).get("id")
                or ""
            )
            if not session_id:
                raise RuntimeError(f"OpenCode session create returned no id: {session}")
            print(f"created_opencode_session={session_id}", flush=True)

            plugins_after = subprocess.run(
                ["opencode", "plugin", "list"],
                cwd=workspace,
                text=True,
                capture_output=True,
                check=False,
                timeout=20,
            )
            print("plugins_after_session=" + plugins_after.stdout.strip(), flush=True)

            command = f"printf HUMANQ_OPENCODE_NATIVE > {marker}"
            body = json.dumps({"agent": "build", "command": command})
            shell_call = subprocess.Popen(
                [
                    "opencode",
                    "api",
                    "POST",
                    f"/api/session/{session_id}/shell",
                    "--data",
                    body,
                ],
                cwd=workspace,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

            item = wait_for_opencode_boundary(
                base_url,
                token,
                session_id=session_id,
                marker=marker,
                shell_call=shell_call,
            )
            request_id = str(item["id"])
            if item.get("status") != "pending":
                raise RuntimeError(f"OpenCode boundary was not pending: {item}")

            context = item.get("context") or {}
            native = context.get("native_handle") or {}
            if native.get("provider") != "opencode":
                raise RuntimeError(f"wrong native provider: {native}")
            if str(native.get("session_id") or "") != session_id:
                raise RuntimeError(
                    f"wrong native session identity: expected {session_id}, got {native}"
                )

            resolved = request_json(
                "POST",
                base_url + f"/v1/requests/{request_id}/resolve",
                token,
                {
                    "actor": "ci-human",
                    "actor_kind": "human",
                    "action": "approve",
                    "values": {"verified": True},
                    "comment": "OpenCode native permission E2E",
                },
            )
            if resolved["request"]["id"] != request_id or resolved["finalized"] is not True:
                raise RuntimeError(f"human:// did not finalize exact request: {resolved}")

            output, _ = shell_call.communicate(timeout=15)
            if shell_call.returncode != 0:
                raise RuntimeError(
                    f"OpenCode shell did not continue after approval ({shell_call.returncode}):\n"
                    + output
                )
            if not marker.exists():
                raise RuntimeError(
                    "OpenCode shell returned after approval but native side effect is missing:\n"
                    + output
                )
            if marker.read_text(encoding="utf-8") != "HUMANQ_OPENCODE_NATIVE":
                raise RuntimeError("OpenCode marker contents were unexpected")

            detail = request_json(
                "GET",
                base_url + f"/v1/requests/{request_id}",
                token,
            )
            events = [event["type"] for event in detail.get("events", [])]
            if "resolved" not in events:
                raise RuntimeError(f"resolved lifecycle event missing: {events}")

            sessions = request_json(
                "GET",
                base_url + "/v1/connectors/sessions?limit=100",
                token,
            )
            observed = [
                row
                for row in sessions.get("sessions", [])
                if row.get("provider") == "opencode"
                and row.get("session_id") == session_id
            ]
            if not observed:
                raise RuntimeError(
                    f"OpenCode native session was not observed by human://: {sessions}"
                )

            print("OPENCODE_NATIVE_PERMISSION_E2E_OK")
            print(f"opencode_session_id={session_id}")
            print(f"human_request_id={request_id}")
            print("lifecycle=" + " -> ".join(events))
            print(f"marker={marker.read_text(encoding='utf-8')}")
        finally:
            if shell_call is not None and shell_call.poll() is None:
                shell_call.kill()
                shell_call.wait(timeout=5)
            if gateway.poll() is None:
                gateway.terminate()
                try:
                    gateway.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    gateway.kill()
                    gateway.wait(timeout=5)


if __name__ == "__main__":
    main()
