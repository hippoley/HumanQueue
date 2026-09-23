from __future__ import annotations

from urllib.parse import urlparse

from .models import ActionOption, RequestKind

HUMAN_TARGETS: dict[str, RequestKind] = {
    "approve": RequestKind.approval,
    "review": RequestKind.review,
    "clarify": RequestKind.input,
    "auth": RequestKind.authenticate,
    "choose": RequestKind.choice,
    "edit": RequestKind.edit,
    "claim": RequestKind.claim,
}

KIND_TO_URI = {
    RequestKind.approval: "human://approve",
    RequestKind.review: "human://review",
    RequestKind.input: "human://clarify",
    RequestKind.authenticate: "human://auth",
    RequestKind.choice: "human://choose",
    RequestKind.edit: "human://edit",
    RequestKind.claim: "human://claim",
}


def parse_human_uri(uri: str) -> tuple[str, RequestKind]:
    parsed = urlparse(uri)
    if parsed.scheme != "human":
        raise ValueError("URI must use the human:// scheme")
    target = (parsed.netloc or parsed.path.lstrip("/")).lower()
    if target not in HUMAN_TARGETS:
        allowed = ", ".join(f"human://{x}" for x in HUMAN_TARGETS)
        raise ValueError(f"unsupported human target: {target!r}. Allowed: {allowed}")
    return target, HUMAN_TARGETS[target]


def uri_for_kind(kind: RequestKind) -> str:
    return KIND_TO_URI[kind]


def default_options(kind: RequestKind) -> list[ActionOption]:
    if kind == RequestKind.approval:
        return [ActionOption(id="approve", label="Approve", style="safe"),ActionOption(id="reject", label="Reject", style="danger")]
    if kind == RequestKind.review:
        return [ActionOption(id="approve", label="Looks good", style="safe"),ActionOption(id="changes", label="Request changes", style="default"),ActionOption(id="reject", label="Reject", style="danger")]
    if kind == RequestKind.authenticate:
        return [ActionOption(id="continue", label="I've authenticated", style="safe"),ActionOption(id="cancel", label="Cancel", style="danger")]
    if kind == RequestKind.claim:
        return [ActionOption(id="claim", label="Take it", style="safe")]
    if kind == RequestKind.edit:
        return [ActionOption(id="submit", label="Save & continue", style="safe"),ActionOption(id="reject", label="Reject", style="danger")]
    if kind == RequestKind.input:
        return [ActionOption(id="submit", label="Send answer", style="safe")]
    return []
