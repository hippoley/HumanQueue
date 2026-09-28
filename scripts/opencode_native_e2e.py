from __future__ import annotations

import json
import os
import socket
import subprocess
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
    with urllib.request.urlopen(req, timeout=3) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for_opencode_server(url: str, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            body = request_json("GET", url + "/api/info")
            if body.get("version"):
                return body
        except Exception as exc:
            last_error = exc
        time.sleep(0.1)
    raise RuntimeError(f"OpenCode server did not become healthy: {last_error}")


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


def _tool_name(body: dict) -> str:
    tools = body.get("tools") or []
    names: list[str] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = str(fn.get("name") or "")
        if not name:
            continue
        names.append(name)
        low = name.lower()
        if low in {"bash", "shell"} or "shell" in low or "bash" in low:
            return name
        params = fn.get("parameters") or {}
        props = params.get("properties") if isinstance(params, dict) else {}
        if isinstance(props, dict) and "command" in props:
            return name
    raise RuntimeError(f"stub model did not receive a shell-like tool; got {names}")


def start_stub_model(port: int, marker: Path, request_log: Path):
    command = f"printf HUMANQ_OPENCODE_NATIVE > {marker}"

    class Handler(BaseHTTPRequestHandler):
        server_version = "humanq-openai-stub/1"

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            return

        def _json(self, status: int, payload: dict) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            if self.path.rstrip("/") == "/v1/models":
                self._json(
                    200,
                    {
                        "object": "list",
                        "data": [
                            {
                                "id": "stub-coder",
                                "object": "model",
                                "created": 0,
                                "owned_by": "humanq-ci",
                            }
                        ],
                    },
                )
                return
            self._json(404, {"error": {"message": "not found"}})

        def do_POST(self) -> None:  # noqa: N802
            if self.path.rstrip("/") != "/v1/chat/completions":
                self._json(404, {"error": {"message": f"unsupported path {self.path}"}})
                return

            length = int(self.headers.get("Content-Length") or "0")
            body = json.loads(self.rfile.read(length) or b"{}")
            with request_log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(body, sort_keys=True, default=str) + "\n")

            messages = body.get("messages") or []
            has_tool_result = any(
                isinstance(message, dict)
                and str(message.get("role") or "").lower() == "tool"
                for message in messages
            )
            stream = bool(body.get("stream"))

            if has_tool_result:
                message = {
                    "role": "assistant",
                    "content": "Native shell completed after human approval.",
                }
                finish = "stop"
            else:
                name = _tool_name(body)
                args = {"command": command}
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_humanq_native",
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(args),
                            },
                        }
                    ],
                }
                finish = "tool_calls"

            if not stream:
                self._json(
                    200,
                    {
                        "id": "chatcmpl-humanq",
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": "stub-coder",
                        "choices": [
                            {
                                "index": 0,
                                "message": message,
                                "finish_reason": finish,
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 1,
                            "completion_tokens": 1,
                            "total_tokens": 2,
                        },
                    },
                )
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            if has_tool_result:
                chunks = [
                    {
                        "id": "chatcmpl-humanq",
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": "stub-coder",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "role": "assistant",
                                    "content": "Native shell completed after human approval.",
                                },
                                "finish_reason": None,
                            }
                        ],
                    },
                    {
                        "id": "chatcmpl-humanq",
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": "stub-coder",
                        "choices": [
                            {"index": 0, "delta": {}, "finish_reason": "stop"}
                        ],
                    },
                ]
            else:
                call = message["tool_calls"][0]
                chunks = [
                    {
                        "id": "chatcmpl-humanq",
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": "stub-coder",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "role": "assistant",
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": call["id"],
                                            "type": "function",
                                            "function": {
                                                "name": call["function"]["name"],
                                                "arguments": call["function"]["arguments"],
                                            },
                                        }
                                    ],
                                },
                                "finish_reason": None,
                            }
                        ],
                    },
                    {
                        "id": "chatcmpl-humanq",
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": "stub-coder",
                        "choices": [
                            {"index": 0, "delta": {}, "finish_reason": "tool_calls"}
                        ],
                    },
                ]

            for chunk in chunks:
                self.wfile.write(
                    ("data: " + json.dumps(chunk, separators=(",", ":")) + "\n\n").encode(
                        "utf-8"
                    )
                )
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def wait_for_opencode_boundary(
    base_url: str,
    token: str,
    *,
    marker: Path,
    child: subprocess.Popen[str],
    timeout: float = 25.0,
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = request_json("GET", base_url + "/v1/queue?status=pending", token)
        for item in body.get("items", []):
            if item.get("source") != "opencode":
                continue
            if child.poll() is not None:
                output = child.stdout.read() if child.stdout else ""
                raise RuntimeError(
                    "OpenCode agent exited before the human decision:\n" + output
                )
            if marker.exists():
                raise RuntimeError("OpenCode shell side effect happened before human approval")
            return item
        if child.poll() is not None:
            output = child.stdout.read() if child.stdout else ""
            raise RuntimeError(
                "OpenCode agent exited without creating a human:// boundary:\n" + output
            )
        time.sleep(0.1)
    output = ""
    if child.stdout:
        try:
            output = child.stdout.read()
        except Exception:
            pass
    raise RuntimeError(
        "timed out waiting for native OpenCode permission boundary\n"
        + ("OpenCode output:\n" + output if output else "")
    )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="humanq-opencode-native-") as temp:
        root = Path(temp)
        workspace = root / "workspace"
        workspace.mkdir()
        marker = workspace / "native-approved.txt"
        request_log = root / "stub-requests.jsonl"
        model_port = free_port()
        opencode_port = free_port()

        (workspace / "opencode.jsonc").write_text(
            json.dumps(
                {
                    "$schema": "https://opencode.ai/config.json",
                    "model": "local/coder",
                    "providers": {
                        "local": {
                            "name": "human:// CI stub",
                            "env": ["HUMANQ_STUB_API_KEY"],
                            "package": "@opencode/ai/providers/openai-compatible",
                            "settings": {
                                "baseURL": f"http://127.0.0.1:{model_port}/v1",
                                "apiKey": "{env:HUMANQ_STUB_API_KEY}"
                            },
                            "models": {
                                "coder": {
                                    "modelID": "stub-coder",
                                    "capabilities": {
                                        "tools": True,
                                        "input": ["text"],
                                        "output": ["text"],
                                    },
                                    "limit": {"context": 32768, "output": 4096},
                                }
                            },
                        }
                    },
                    "permissions": [
                        {"action": "shell", "resource": "*", "effect": "ask"}
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
        stub = start_stub_model(model_port, marker, request_log)
        server: subprocess.Popen[str] | None = None
        child: subprocess.Popen[str] | None = None

        try:
            wait_for_health(base_url)

            child_env = os.environ.copy()
            child_env["OPENCODE_DISABLE_MODELS_FETCH"] = "1"
            child_env["OPENCODE_DISABLE_LSP_DOWNLOAD"] = "1"
            child_env["HUMANQ_STUB_API_KEY"] = "humanq-ci-dummy"

            debug_config = subprocess.run(
                ["opencode", "debug", "config"],
                cwd=workspace,
                env=child_env,
                text=True,
                capture_output=True,
                check=False,
                timeout=20,
            )
            print("resolved_config_begin", flush=True)
            print(debug_config.stdout, flush=True)
            print(debug_config.stderr, flush=True)
            print("resolved_config_end", flush=True)
            if debug_config.returncode != 0:
                raise RuntimeError(
                    "OpenCode rejected the local provider config:\n"
                    + debug_config.stdout
                    + "\n"
                    + debug_config.stderr
                )

            model_catalog = subprocess.run(
                ["opencode", "models"],
                cwd=workspace,
                env=child_env,
                text=True,
                capture_output=True,
                check=False,
                timeout=20,
            )
            print("model_catalog_begin", flush=True)
            print(model_catalog.stdout, flush=True)
            print(model_catalog.stderr, flush=True)
            print("model_catalog_end", flush=True)
            if model_catalog.returncode != 0:
                raise RuntimeError(
                    "OpenCode could not resolve its model catalog:\n"
                    + model_catalog.stdout
                    + "\n"
                    + model_catalog.stderr
                )
            if "local/coder" not in model_catalog.stdout:
                raise RuntimeError(
                    "OpenCode resolved config but did not activate local/coder:\n"
                    + model_catalog.stdout
                    + "\n"
                    + model_catalog.stderr
                )

            server = subprocess.Popen(
                [
                    "opencode",
                    "serve",
                    "--hostname",
                    "127.0.0.1",
                    "--port",
                    str(opencode_port),
                ],
                cwd=workspace,
                env=child_env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            server_url = f"http://127.0.0.1:{opencode_port}"
            server_info = wait_for_opencode_server(server_url)
            print("attached_server_info=" + json.dumps(server_info, sort_keys=True), flush=True)

            child = subprocess.Popen(
                [
                    "opencode",
                    "run",
                    "--attach",
                    server_url,
                    "--model",
                    "local/coder",
                    (
                        "Use the shell tool to create the requested marker. "
                        "Do not skip the tool call."
                    ),
                ],
                cwd=workspace,
                env=child_env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

            item = wait_for_opencode_boundary(
                base_url,
                token,
                marker=marker,
                child=child,
            )
            request_id = str(item["id"])
            if item.get("status") != "pending":
                raise RuntimeError(f"OpenCode boundary was not pending: {item}")

            context = item.get("context") or {}
            native = context.get("native_handle") or {}
            if native.get("provider") != "opencode":
                raise RuntimeError(f"wrong native provider: {native}")
            session_id = str(native.get("session_id") or "")
            if not session_id:
                raise RuntimeError(f"OpenCode native session identity is missing: {native}")

            if marker.exists():
                raise RuntimeError("native marker exists before human approval")

            resolved = request_json(
                "POST",
                base_url + f"/v1/requests/{request_id}/resolve",
                token,
                {
                    "actor": "ci-human",
                    "actor_kind": "human",
                    "action": "approve",
                    "values": {"verified": True},
                    "comment": "OpenCode native agent permission E2E",
                },
            )
            if resolved["request"]["id"] != request_id or resolved["finalized"] is not True:
                raise RuntimeError(f"human:// did not finalize exact request: {resolved}")

            output, _ = child.communicate(timeout=25)
            if child.returncode != 0:
                requests = request_log.read_text(encoding="utf-8") if request_log.exists() else ""
                raise RuntimeError(
                    f"OpenCode agent did not continue after approval ({child.returncode}):\n"
                    f"{output}\nstub requests:\n{requests}"
                )
            if not marker.exists():
                requests = request_log.read_text(encoding="utf-8") if request_log.exists() else ""
                raise RuntimeError(
                    "OpenCode agent completed but native shell side effect is missing:\n"
                    f"{output}\nstub requests:\n{requests}"
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
            print("agent_output=" + output.strip().replace("\n", " ")[:500])
        finally:
            if child is not None and child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            if server is not None and server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
            stub.shutdown()
            stub.server_close()
            if gateway.poll() is None:
                gateway.terminate()
                try:
                    gateway.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    gateway.kill()
                    gateway.wait(timeout=5)


if __name__ == "__main__":
    main()
