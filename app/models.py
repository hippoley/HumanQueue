from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, HttpUrl, model_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RequestKind(str, Enum):
    approval = "approval"
    choice = "choice"
    input = "input"
    edit = "edit"
    authenticate = "authenticate"
    review = "review"
    claim = "claim"


class RequestStatus(str, Enum):
    queued = "queued"
    claimed = "claimed"
    resolved = "resolved"
    cancelled = "cancelled"
    expired = "expired"
    superseded = "superseded"


class SurfaceMode(str, Enum):
    interrupt = "interrupt"
    queue = "queue"
    batch = "batch"
    defer = "defer"


class ActionOption(BaseModel):
    id: str
    label: str
    style: Literal["safe", "default", "danger"] = "default"


class AttentionSignals(BaseModel):
    urgency: float = Field(0.5, ge=0, le=1)
    unblock_value: float = Field(0.7, ge=0, le=1)
    blast_radius: float = Field(0.2, ge=0, le=1)
    risk_if_wrong: float = Field(0.5, ge=0, le=1)
    risk_if_delayed: float = Field(0.2, ge=0, le=1)
    human_effort_seconds: int = Field(30, ge=1, le=3600)
    downstream_blocked: int = Field(0, ge=0, le=100000)


class ResumeTarget(BaseModel):
    mode: Literal["none", "webhook"] = "none"
    url: HttpUrl | None = None
    secret: str | None = None


class RoutePolicy(BaseModel):
    mode: Literal["single", "any_of", "quorum", "all_of"] = "single"
    actors: list[str] = []
    quorum: int = 1

    @model_validator(mode="after")
    def validate_quorum(self):
        if self.mode == "all_of" and self.actors:
            self.quorum = len(self.actors)
        elif self.mode == "any_of":
            self.quorum = 1
        elif self.mode == "quorum" and self.actors and self.quorum > len(self.actors):
            raise ValueError("quorum cannot exceed actor count")
        return self


class BudgetPolicy(BaseModel):
    group: str = "default"
    max_interrupts_per_hour: int = Field(8, ge=0, le=10000)
    min_interrupt_priority: float = Field(70, ge=0, le=100)
    min_queue_priority: float = Field(25, ge=0, le=100)
    batch_below_priority: float = Field(55, ge=0, le=100)


class HumanAsk(BaseModel):
    uri: Literal[
        "human://approve", "human://review", "human://clarify", "human://auth",
        "human://choose", "human://edit", "human://claim",
    ]
    source: str
    ref: str
    title: str
    summary: str = ""
    why_now: str | None = None
    context: dict[str, Any] = {}
    options: list[ActionOption] = []
    fields_schema: dict[str, Any] | None = None
    urgency: float = Field(.5, ge=0, le=1)
    unblock: float = Field(.7, ge=0, le=1)
    risk: float = Field(.5, ge=0, le=1)
    risk_if_delayed: float = Field(.2, ge=0, le=1)
    blast_radius: float = Field(.2, ge=0, le=1)
    seconds: int = Field(20, ge=1, le=3600)
    downstream: int = Field(1, ge=0)
    attention_group: str = "default"
    batch_key: str | None = None
    supersession_key: str | None = None
    policy_key: str | None = None
    idempotency_key: str | None = None
    resume: ResumeTarget = ResumeTarget()


class AttentionRequestCreate(BaseModel):
    source: str
    source_ref: str
    title: str
    summary: str = ""
    kind: RequestKind
    why_now: str | None = None
    options: list[ActionOption] = []
    fields_schema: dict[str, Any] | None = None
    context: dict[str, Any] = {}
    signals: AttentionSignals = AttentionSignals()
    route: RoutePolicy = RoutePolicy()
    due_at: datetime | None = None
    idempotency_key: str | None = None
    batch_key: str | None = None
    supersession_key: str | None = None
    policy_key: str | None = None
    attention_group: str = "default"
    resume: ResumeTarget = ResumeTarget()


class AttentionRequest(AttentionRequestCreate):
    id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    status: RequestStatus = RequestStatus.queued
    priority: float = 0
    surface_mode: SurfaceMode = SurfaceMode.queue
    claimed_by: str | None = None
    resolution: dict[str, Any] | None = None
    resolved_at: datetime | None = None
    superseded_by: str | None = None
    quorum_progress: dict[str, Any] = {}


class ResolveIn(BaseModel):
    actor: str = "human"
    action: str
    values: dict[str, Any] = {}
    comment: str | None = None


class ClaimIn(BaseModel):
    actor: str


class ImportEnvelope(BaseModel):
    adapter: Literal["generic", "a2a", "mcp", "openai", "github"]
    payload: dict[str, Any]


class BatchResolve(BaseModel):
    actor: str = "human"
    action: str
    values: dict[str, Any] = {}
    comment: str | None = None
