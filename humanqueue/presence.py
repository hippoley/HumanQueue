from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


PresenceState = Literal[
    "idle",
    "running",
    "waiting_human",
    "waiting_external",
    "completed",
    "failed",
    "offline",
    "unknown",
]


@dataclass
class SessionPresence:
    source_id: str
    provider: str
    account: str
    session_id: str
    state: PresenceState = "unknown"
    title: str | None = None
    workspace: str | None = None
    last_user: str | None = None
    last_agent: str | None = None
    current_action: str | None = None
    waiting_reason: str | None = None
    progress: float | None = None
    updated_at: str | None = None
    native: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SourceAccount:
    id: str
    provider: str
    account: str
    mode: str
    endpoint: str | None = None
    enabled: bool = True
    config: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
