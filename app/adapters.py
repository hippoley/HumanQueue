from __future__ import annotations

from datetime import datetime
from typing import Any

from .models import (
    ActionOption,
    AttentionRequestCreate,
    AttentionSignals,
    RequestKind,
    ResumeTarget,
)


def from_generic(payload: dict[str, Any]) -> AttentionRequestCreate:
    return AttentionRequestCreate.model_validate(payload)


def from_a2a(payload: dict[str, Any]) -> AttentionRequestCreate:
    # Expected shape: {task: {id,status:{state,message}}, agent?, callback_url?}
    task = payload.get("task", payload)
    status = task.get("status", {})
    state = status.get("state", "input-required")
    kind = RequestKind.authenticate if state == "auth-required" else RequestKind.input
    message = status.get("message") or payload.get("message") or "Agent needs human input"
    return AttentionRequestCreate(
        source="a2a",
        source_ref=str(task.get("id", payload.get("id", "unknown"))),
        title=f"A2A task needs {state}",
        summary=str(message),
        kind=kind,
        context=payload,
        options=[ActionOption(id="continue", label="Continue", style="safe")],
        signals=AttentionSignals(urgency=0.55, unblock_value=0.8, downstream_blocked=1),
        supersession_key=f"a2a:{task.get('id', payload.get('id', 'unknown'))}",
        resume=ResumeTarget(mode="webhook", url=payload["callback_url"]) if payload.get("callback_url") else ResumeTarget(),
    )


def from_mcp(payload: dict[str, Any]) -> AttentionRequestCreate:
    # Normalizes elicitation/create form or URL mode.
    params = payload.get("params", payload)
    requested_schema = params.get("requestedSchema") or params.get("requested_schema")
    url = params.get("url")
    kind = RequestKind.authenticate if url else RequestKind.input
    return AttentionRequestCreate(
        source="mcp",
        source_ref=str(params.get("elicitationId") or payload.get("id") or "elicitation"),
        title="MCP elicitation",
        summary=str(params.get("message", "MCP server requests human input")),
        kind=kind,
        fields_schema=requested_schema,
        context=payload,
        signals=AttentionSignals(urgency=0.5, unblock_value=0.75, downstream_blocked=1),
        batch_key="mcp-elicitation" if not url else None,
    )


def from_openai(payload: dict[str, Any]) -> AttentionRequestCreate:
    # Expected interruption-like payload. Kept loose on purpose to tolerate SDK evolution.
    tool = payload.get("tool_name") or payload.get("name") or "tool call"
    return AttentionRequestCreate(
        source="openai-agents",
        source_ref=str(payload.get("run_id") or payload.get("id") or "run"),
        title=f"Approve {tool}",
        summary=str(payload.get("reason") or payload.get("message") or f"Agent paused before {tool}"),
        kind=RequestKind.approval,
        options=[
            ActionOption(id="approve", label="Approve", style="safe"),
            ActionOption(id="reject", label="Reject", style="danger"),
        ],
        context=payload,
        signals=AttentionSignals(
            urgency=float(payload.get("urgency", 0.5)),
            unblock_value=float(payload.get("unblock_value", 0.8)),
            risk_if_wrong=float(payload.get("risk", 0.7)),
            downstream_blocked=int(payload.get("downstream_blocked", 1)),
        ),
        supersession_key=f"openai:{payload.get('run_id') or payload.get('id') or 'run'}",
    )


def from_github(payload: dict[str, Any]) -> AttentionRequestCreate:
    deployment = payload.get("deployment", {})
    repo = (payload.get("repository") or {}).get("full_name", "repository")
    env = deployment.get("environment") or payload.get("environment", "production")
    return AttentionRequestCreate(
        source="github",
        source_ref=str(deployment.get("id") or payload.get("id") or "deployment"),
        title=f"Deploy {repo} → {env}",
        summary="A protected deployment is waiting for a human gate.",
        kind=RequestKind.approval,
        options=[
            ActionOption(id="approve", label="Approve deploy", style="safe"),
            ActionOption(id="reject", label="Reject", style="danger"),
        ],
        context=payload,
        signals=AttentionSignals(
            urgency=0.65,
            unblock_value=0.9,
            blast_radius=0.8 if env == "production" else 0.4,
            risk_if_wrong=0.85 if env == "production" else 0.55,
            downstream_blocked=3,
            human_effort_seconds=20,
        ),
        supersession_key=f"github-deploy:{repo}:{env}",
    )


ADAPTERS = {
    "generic": from_generic,
    "a2a": from_a2a,
    "mcp": from_mcp,
    "openai": from_openai,
    "github": from_github,
}
