from __future__ import annotations

import json
import sys
from typing import Any

from humanqueue.client import HumanQueue

PROTOCOL_VERSION = "2025-06-18"


def _tool_definition() -> dict[str, Any]:
    return {
        "name": "human_ask",
        "title": "Ask a human",
        "description": "Pause this workflow and ask the user's Human Queue for approval, clarification, review, or a choice.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "uri": {
                    "type": "string",
                    "enum": [
                        "human://approve",
                        "human://review",
                        "human://clarify",
                        "human://auth",
                        "human://choose",
                        "human://edit",
                    ],
                },
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "source": {"type": "string"},
                "ref": {"type": "string"},
                "context": {"type": "object"},
                "options": {"type": "array"},
                "fields_schema": {"type": "object"},
                "risk": {"type": "number", "minimum": 0, "maximum": 1},
                "seconds": {"type": "integer", "minimum": 1},
            },
            "required": ["uri", "title"],
        },
    }


def _result(data: dict[str, Any], is_error: bool = False) -> dict[str, Any]:
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": data,
        "isError": is_error,
    }


def _call_human(arguments: dict[str, Any]) -> dict[str, Any]:
    client = HumanQueue()
    source = arguments.get("source") or "mcp-agent"
    ref = arguments.get("ref") or "mcp-human-ask"
    try:
        decision = client.ask(
            arguments.get("uri") or "human://clarify",
            source=str(source),
            ref=str(ref),
            title=str(arguments.get("title") or "Human input required"),
            summary=str(arguments.get("summary") or ""),
            context=arguments.get("context") or {},
            options=arguments.get("options") or [],
            fields_schema=arguments.get("fields_schema"),
            risk=float(arguments.get("risk", 0.4)),
            seconds=int(arguments.get("seconds", 15)),
            wait=True,
            wait_timeout=None,
            poll_interval=0.8,
        )
        return _result({"status": "resolved", "decision": decision})
    except Exception as exc:
        return _result({"status": "error", "message": str(exc)}, is_error=True)


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    if "id" not in message:
        return None

    mid = message["id"]
    method = message.get("method")
    params = message.get("params") or {}

    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in {"2025-06-18", "2025-03-26"} else PROTOCOL_VERSION
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "human-queue", "version": "0.5.0"},
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": [_tool_definition()]}}

    if method == "tools/call":
        name = params.get("name")
        if name != "human_ask":
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "error": {"code": -32602, "message": f"Unknown tool: {name}"},
            }
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": _call_human(params.get("arguments") or {}),
        }

    return {
        "jsonrpc": "2.0",
        "id": mid,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def run_stdio() -> int:
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
            response = handle(message)
            if response is not None:
                sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
                sys.stdout.flush()
        except Exception as exc:
            error = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32603, "message": str(exc)},
            }
            sys.stdout.write(json.dumps(error, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    return 0
