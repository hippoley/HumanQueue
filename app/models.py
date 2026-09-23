from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator


class RequestKind(str, Enum):
    approval = "approval"
    choice = "choice"
    input = "input"
    edit = "edit"
    authenticate = "authenticate"
    review = "review"
    claim = "claim"


class RequestStatus(str, Enum):
    pending = "pending"
    claimed = "claimed"
    resolved = "resolved"
    expired = "expired"
    cancelled = "cancelled"
    superseded = "superseded"


class SurfaceMode(str, Enum):
    interrupt = "interrupt"
    queue = "queue"
    batch = "batch"
    defer = "defer"


class ActionOption(BaseModel):
    id: str
    label: str
    style: Literal["default", "safe", "danger"] = "default"


class ResumeTarget(BaseModel):
    mode: Literal["webhook", "none"] = "none"
    url: HttpUrl | None = None
    secret: str | None = Field(default=None, repr=False)


class AttentionSignals(BaseModel):
    urgency: float = Field(default=0.5, ge=0, le=1)
    unblock_value: float = Field(default=0.5, ge=0, le=1)
    blast_radius: float = Field(default=0.2, ge=0, le=1)
    risk_if_wrong: float = Field(default=0.5, ge=0, le=1)
    risk_if_delayed: float = Field(default=0.2, ge=0, le=1)
    human_effort_seconds: int = Field(default=30, ge=1, le=86400)
    downstream_blocked: int = Field(default=1, ge=0, le=100000)


class RoutePolicy(BaseModel):
    mode: Literal["single", "any_of", "quorum", "all_of"] = "single"
    actors: list[str] = Field(default_factory=list)
    quorum: int = Field(default=1, ge=1, le=1000)
    team: str | None = None
    escalation_after_seconds: int | None = Field(default=None, ge=60, le=604800)

    @model_validator(mode="after")
    def validate_quorum(self):
        if self.mode == "all_of" and self.actors:
            self.quorum = len(self.actors)
        if self.mode == "quorum" and self.actors and self.quorum > len(self.actors):
            raise ValueError("quorum cannot exceed number of allowed actors")
        return self


class AttentionRequestCreate(BaseModel):
    source: str = Field(description="Platform or runtime, e.g. github, jira, openai-agents")
    source_ref: str = Field(description="Native request/task/session identifier")
    title: str
    summary: str
    kind: RequestKind
    actor_hint: str | None = None
    due_at: datetime | None = None
    options: list[ActionOption] = Field(default_factory=list)
    fields_schema: dict[str, Any] | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    signals: AttentionSignals = Field(default_factory=AttentionSignals)
    route: RoutePolicy = Field(default_factory=RoutePolicy)
    resume: ResumeTarget = Field(default_factory=ResumeTarget)
    idempotency_key: str | None = None
    batch_key: str | None = None
    supersession_key: str | None = None
    policy_key: str | None = None
    attention_group: str = "default"


class AttentionRequest(AttentionRequestCreate):
    id: str
    status: RequestStatus
    priority: float
    surface_mode: SurfaceMode = SurfaceMode.queue
    created_at: datetime
    updated_at: datetime
    claimed_by: str | None = None
    resolved_by: str | None = None
    resolution: dict[str, Any] | None = None
    superseded_by: str | None = None
    quorum_progress: dict[str, Any] = Field(default_factory=dict)


class HumanAsk(BaseModel):
    """Small protocol-facing envelope for human:// interrupts.

    It deliberately exposes only the fields a caller needs for a fast integration.
    The server expands it into the richer AttentionRequestCreate model.
    """
    uri: str = Field(description="human://approve, human://review, human://clarify, human://auth, human://choose, human://edit or human://claim")
    source: str
    ref: str
    title: str
    summary: str = ""
    why_now: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    options: list[ActionOption] = Field(default_factory=list)
    fields_schema: dict[str, Any] | None = None
    urgency: float = Field(default=0.5, ge=0, le=1)
    unblock: float = Field(default=0.7, ge=0, le=1)
    risk: float = Field(default=0.5, ge=0, le=1)
    risk_if_delayed: float = Field(default=0.2, ge=0, le=1)
    blast_radius: float = Field(default=0.2, ge=0, le=1)
    seconds: int = Field(default=20, ge=1, le=86400)
    downstream: int = Field(default=1, ge=0, le=100000)
    due_at: datetime | None = None
    resume: ResumeTarget = Field(default_factory=ResumeTarget)
    route: RoutePolicy = Field(default_factory=RoutePolicy)
    idempotency_key: str | None = None
    batch_key: str | None = None
    supersession_key: str | None = None
    policy_key: str | None = None
    attention_group: str = "default"


class ResolveRequest(BaseModel):
    actor: str
    action: str | None = None
    values: dict[str, Any] = Field(default_factory=dict)
    comment: str | None = None


class ClaimRequest(BaseModel):
    actor: str


class ImportEnvelope(BaseModel):
    adapter: Literal["generic", "a2a", "mcp", "openai", "github"]
    payload: dict[str, Any]


class BudgetPolicy(BaseModel):
    group: str = "default"
    max_interrupts_per_hour: int = Field(default=6, ge=0, le=1000)
    min_interrupt_priority: float = Field(default=72, ge=0, le=100)
    min_queue_priority: float = Field(default=35, ge=0, le=100)
    batch_below_priority: float = Field(default=55, ge=0, le=100)
    max_batch_wait_seconds: int = Field(default=900, ge=30, le=86400)


class BatchResolveRequest(BaseModel):
    actor: str
    action: str
    comment: str | None = None
