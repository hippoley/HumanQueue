from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def send(proc: subprocess.Popen[str], message: dict[str, Any]) -> None:
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
    proc.stdin.flush()


def read_until_id(proc: subprocess.Popen[str], wanted: int, timeout: float = 10.0) -> dict[str, Any]:
    assert proc.stdout is not None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                stderr = proc.stderr.read() if proc.stderr else ""
                raise RuntimeError(f"codex app-server exited {proc.returncode}: {stderr}")
            time.sleep(0.05)
            continue
        message = json.loads(line)
        if message.get("id") == wanted:
            return message
    raise TimeoutError(f"timed out waiting for JSON-RPC id={wanted}")


def hooks_for_cwd(result: dict[str, Any], cwd: str) -> list[dict[str, Any]]:
    rows = (result.get("result") or {}).get("data") or []
    for row in rows:
        if row.get("cwd") == cwd:
            return row.get("hooks") or []
    return []


def human_hooks(hooks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [h for h in hooks if "humanqueue connector hook" in str(h.get("command") or "").replace("-m ", "")]


def main() -> None:
    cwd = str(Path.cwd().resolve())
    codex = os.environ.get("CODEX_BIN", "codex")
    proc = subprocess.Popen(
        [codex, "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        send(proc, {
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "humanq-codex-probe", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True},
            },
        })
        init = read_until_id(proc, 1)
        if "error" in init:
            raise RuntimeError(f"initialize failed: {init}")

        send(proc, {"method": "initialized"})

        send(proc, {"id": 2, "method": "hooks/list", "params": {"cwds": [cwd]}})
        first = read_until_id(proc, 2)
        if "error" in first:
            raise RuntimeError(f"hooks/list failed: {first}")

        found = human_hooks(hooks_for_cwd(first, cwd))
        if not found:
            raise RuntimeError(f"Codex binary did not discover human:// hooks: {first}")

        permission = [h for h in found if h.get("eventName") == "permissionRequest"]
        if not permission:
            raise RuntimeError(f"Codex binary did not discover PermissionRequest hook: {found}")

        p = permission[0]
        print("CODEX_BINARY_DISCOVERED_HUMANQ_HOOK")
        print("key=" + str(p.get("key")))
        print("trust_before=" + str(p.get("trustStatus")))
        print("current_hash=" + str(p.get("currentHash")))
        print("timeout_sec=" + str(p.get("timeoutSec")))
        print("status_message=" + str(p.get("statusMessage")))

        if p.get("trustStatus") not in {"untrusted", "modified", "trusted", "managed"}:
            raise RuntimeError(f"unexpected hook trust status: {p}")

        if p.get("trustStatus") in {"untrusted", "modified"}:
            send(proc, {
                "id": 3,
                "method": "config/batchWrite",
                "params": {
                    "edits": [{
                        "keyPath": "hooks.state",
                        "value": {str(p["key"]): {"trusted_hash": str(p["currentHash"])}},
                        "mergeStrategy": "upsert",
                    }],
                    "filePath": None,
                    "expectedVersion": None,
                    "reloadUserConfig": True,
                },
            })
            trusted = read_until_id(proc, 3)
            if "error" in trusted:
                raise RuntimeError(f"config/batchWrite trust failed: {trusted}")

            send(proc, {"id": 4, "method": "hooks/list", "params": {"cwds": [cwd]}})
            second = read_until_id(proc, 4)
            if "error" in second:
                raise RuntimeError(f"second hooks/list failed: {second}")
            found2 = human_hooks(hooks_for_cwd(second, cwd))
            permission2 = [h for h in found2 if h.get("eventName") == "permissionRequest"]
            if not permission2:
                raise RuntimeError(f"PermissionRequest hook disappeared after trust: {found2}")
            print("trust_after=" + str(permission2[0].get("trustStatus")))
            if permission2[0].get("trustStatus") not in {"trusted", "managed"}:
                raise RuntimeError(f"Codex did not accept trusted hash: {permission2[0]}")
        else:
            print("trust_after=" + str(p.get("trustStatus")))

        print("CODEX_NATIVE_HOOK_DISCOVERY_AND_TRUST_OK")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


if __name__ == "__main__":
    main()
