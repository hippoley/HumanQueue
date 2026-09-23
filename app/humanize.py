from __future__ import annotations

from .models import AttentionRequestCreate, AttentionSignals, HumanAsk
from .protocol import default_options, parse_human_uri


def to_attention_request(ask: HumanAsk) -> AttentionRequestCreate:
    _, kind = parse_human_uri(ask.uri)
    context = dict(ask.context)
    if ask.why_now:
        context.setdefault("why_now", ask.why_now)
    options = ask.options or default_options(kind)
    return AttentionRequestCreate(
        source=ask.source,
        source_ref=ask.ref,
        title=ask.title,
        summary=ask.summary or ask.why_now or "A machine needs human judgment before it can continue.",
        kind=kind,
        due_at=ask.due_at,
        options=options,
        fields_schema=ask.fields_schema,
        context=context,
        signals=AttentionSignals(
            urgency=ask.urgency,
            unblock_value=ask.unblock,
            blast_radius=ask.blast_radius,
            risk_if_wrong=ask.risk,
            risk_if_delayed=ask.risk_if_delayed,
            human_effort_seconds=ask.seconds,
            downstream_blocked=ask.downstream,
        ),
        route=ask.route,
        resume=ask.resume,
        idempotency_key=ask.idempotency_key,
        batch_key=ask.batch_key,
        supersession_key=ask.supersession_key,
        policy_key=ask.policy_key,
        attention_group=ask.attention_group,
    )
