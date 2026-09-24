from __future__ import annotations

import json
import sys

import httpx
from typing import Any

from humanqueue import __version__
from humanqueue.client import HumanQueue
from humanqueue.config import gateway_token, gateway_url

PROTOCOL_VERSION = "2025-06-18"


def _tool_definitions() -> list[dict[str, Any]]:
    return [
        {
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
        },
        {
            "name": "human_presence_list",
            "title": "List agent sessions",
            "description": "Read normalized session status across the user's connected agent accounts and runtimes.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "state": {
                        "type": "string",
                        "enum": [
                            "idle","running","waiting_human","waiting_external",
                            "completed","failed","offline","unknown"
                        ],
                    },
                    "provider": {"type": "string"},
                    "account": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                },
            },
        },
        {
            "name": "human_presence_summary",
            "title": "Summarize agent fleet status",
            "description": "Return counts and the highest-attention sessions across all connected agent sources.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
            },
        },
    ]


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


def _gateway_headers() -> dict[str, str]:
    token = gateway_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _call_presence(arguments: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": int(arguments.get("limit", 100))}
    if arguments.get("state"):
        params["state"] = arguments["state"]
    response = httpx.get(
        gateway_url() + "/v1/presence/sessions",
        headers=_gateway_headers(),
        params=params,
        timeout=4,
    )
    response.raise_for_status()
    rows = response.json().get("sessions", [])
    provider = str(arguments.get("provider") or "").strip()
    account = str(arguments.get("account") or "").strip()
    if provider:
        rows = [row for row in rows if row.get("provider") == provider]
    if account:
        rows = [row for row in rows if row.get("account") == account]
    return _result({"sessions": rows, "count": len(rows)})


def _call_presence_summary(arguments: dict[str, Any]) -> dict[str, Any]:
    response = httpx.get(
        gateway_url() + "/v1/presence/sessions",
        headers=_gateway_headers(),
        params={"limit": 200},
        timeout=4,
    )
    response.raise_for_status()
    rows = response.json().get("sessions", [])
    counts: dict[str, int] = {}
    for row in rows:
        state = str(row.get("state") or "unknown")
        counts[state] = counts.get(state, 0) + 1
    attention = [
        row for row in rows
        if row.get("state") in {"waiting_human", "failed", "waiting_external"}
    ][: int(arguments.get("limit", 20))]
    return _result({
        "total": len(rows),
        "counts": counts,
        "needs_attention": attention,
    })


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
                "serverInfo": {"name": "human-queue", "version": __version__},
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": _tool_definitions()}}

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name == "human_ask":
            result = _call_human(arguments)
        elif name == "human_presence_list":
            try:
                result = _call_presence(arguments)
            except Exception as exc:
                result = _result({"status": "error", "message": str(exc)}, is_error=True)
        elif name == "human_presence_summary":
            try:
                result = _call_presence_summary(arguments)
            except Exception as exc:
                result = _result({"status": "error", "message": str(exc)}, is_error=True)
        else:
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "error": {"code": -32602, "message": f"Unknown tool: {name}"},
            }
        return {"jsonrpc": "2.0", "id": mid, "result": result}

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
