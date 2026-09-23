from __future__ import annotations

from datetime import datetime, timezone
from math import log1p

from .models import AttentionRequestCreate, BudgetPolicy, SurfaceMode


def _deadline_pressure(due_at: datetime | None) -> float:
    if not due_at:
        return 0.0
    now = datetime.now(timezone.utc)
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=timezone.utc)
    seconds = (due_at - now).total_seconds()
    if seconds <= 0:
        return 1.0
    if seconds <= 15 * 60:
        return 0.9
    if seconds <= 60 * 60:
        return 0.7
    if seconds <= 4 * 60 * 60:
        return 0.5
    if seconds <= 24 * 60 * 60:
        return 0.25
    return 0.05


def priority_score(req: AttentionRequestCreate) -> float:
    """Expected-value-of-intervention style ranking.

    High risk raises human visibility. It never authorizes execution.
    """
    s = req.signals
    blockers = min(1.0, log1p(s.downstream_blocked) / log1p(50)) if s.downstream_blocked else 0.0
    deadline = _deadline_pressure(req.due_at)
    attention_cost = min(1.0, s.human_effort_seconds / 300.0)

    raw = (
        0.23 * s.urgency
        + 0.23 * s.unblock_value
        + 0.14 * blockers
        + 0.12 * deadline
        + 0.11 * s.risk_if_delayed
        + 0.12 * s.risk_if_wrong
        + 0.08 * s.blast_radius
        - 0.10 * attention_cost
    )
    return round(max(0.0, min(1.0, raw)) * 100, 2)


def choose_surface(priority: float, req: AttentionRequestCreate, budget: BudgetPolicy, interrupts_last_hour: int) -> SurfaceMode:
    """Choose *how* to surface a request, never how to decide it."""
    # Security/auth and high consequence operations stay visible even when noisy.
    hard_interrupt = req.kind.value == "authenticate" or req.signals.risk_if_wrong >= 0.92
    if hard_interrupt or (priority >= budget.min_interrupt_priority and interrupts_last_hour < budget.max_interrupts_per_hour):
        return SurfaceMode.interrupt
    if req.batch_key and priority < budget.batch_below_priority:
        return SurfaceMode.batch
    if priority < budget.min_queue_priority and req.signals.risk_if_wrong < 0.65 and req.signals.risk_if_delayed < 0.65:
        return SurfaceMode.defer
    return SurfaceMode.queue
