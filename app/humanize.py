from __future__ import annotations

from .models import ActionOption, AttentionRequestCreate, AttentionSignals, HumanAsk, RequestKind

URI_TO_KIND = {
    "human://approve": RequestKind.approval,
    "human://review": RequestKind.review,
    "human://clarify": RequestKind.input,
    "human://auth": RequestKind.authenticate,
    "human://choose": RequestKind.choice,
    "human://edit": RequestKind.edit,
    "human://claim": RequestKind.claim,
}


def default_options(uri: str) -> list[ActionOption]:
    if uri == "human://approve":
        return [ActionOption(id="approve", label="Approve", style="safe"), ActionOption(id="reject", label="Reject", style="danger")]
    if uri == "human://review":
        return [ActionOption(id="accept", label="Accept", style="safe"), ActionOption(id="changes", label="Request changes"), ActionOption(id="reject", label="Reject", style="danger")]
    if uri == "human://claim":
        return [ActionOption(id="claim", label="Claim", style="safe")]
    if uri == "human://auth":
        return [ActionOption(id="done", label="I've completed it", style="safe"), ActionOption(id="cancel", label="Cancel", style="danger")]
    return []


def to_attention_request(ask: HumanAsk) -> AttentionRequestCreate:
    kind = URI_TO_KIND[ask.uri]
    return AttentionRequestCreate(
        source=ask.source,
        source_ref=ask.ref,
        title=ask.title,
        summary=ask.summary,
        kind=kind,
        why_now=ask.why_now,
        options=ask.options or default_options(ask.uri),
        fields_schema=ask.fields_schema,
        context=ask.context,
        signals=AttentionSignals(
            urgency=ask.urgency,
            unblock_value=ask.unblock,
            risk_if_wrong=ask.risk,
            risk_if_delayed=ask.risk_if_delayed,
            blast_radius=ask.blast_radius,
            human_effort_seconds=ask.seconds,
            downstream_blocked=ask.downstream,
        ),
        idempotency_key=ask.idempotency_key,
        batch_key=ask.batch_key,
        supersession_key=ask.supersession_key,
        policy_key=ask.policy_key,
        attention_group=ask.attention_group,
        resume=ask.resume,
    )
