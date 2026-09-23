from __future__ import annotations

from .humanize import to_attention_request
from .models import ActionOption, BudgetPolicy, HumanAsk
from .store import Store


def seed_wow(store: Store, reset: bool = False) -> list[str]:
    if reset:
        store.clear_all()
    store.set_budget(BudgetPolicy(group="demo", max_interrupts_per_hour=3, min_interrupt_priority=61, min_queue_priority=30, batch_below_priority=58))
    asks = [
        HumanAsk(
            uri="human://approve", source="codex", ref="session-7",
            title="Delete production cache keys?",
            summary="Codex is ready to run a destructive Redis cleanup command.",
            why_now="Execution is paused at the shell boundary.", urgency=.92, unblock=.95,
            risk=.98, blast_radius=.92, seconds=8, downstream=1,
            supersession_key="codex:session-7:shell", attention_group="demo",
            context={"command":"redis-cli --scan --pattern 'legacy:*' | xargs redis-cli del","cwd":"/srv/api"},
        ),
        HumanAsk(
            uri="human://approve", source="github", ref="deploy-2841",
            title="Deploy api@2.4.0 to production",
            summary="The release passed CI and is waiting at the protected environment gate.",
            why_now="Four downstream jobs are blocked on this deployment.", urgency=.82, unblock=.98,
            risk=.78, blast_radius=.80, seconds=6, downstream=4,
            supersession_key="github:acme/api:production", attention_group="demo",
        ),
        HumanAsk(
            uri="human://approve", source="support-agent", ref="ticket-1042",
            title="Refund $149 to a customer?",
            summary="The amount is above the support agent's automatic refund limit.",
            why_now="The customer conversation is waiting for a decision.", urgency=.64, unblock=.80,
            risk=.55, seconds=12, downstream=1, attention_group="demo",
            options=[ActionOption(id="approve",label="Approve refund",style="safe"),ActionOption(id="reject",label="Reject",style="danger")],
        ),
        HumanAsk(
            uri="human://clarify", source="mcp", ref="elicitation-91",
            title="Which customer_id should the research agent use?",
            summary="Two records match the company name and the agent refuses to guess.",
            why_now="Research can continue as soon as one identifier is selected.", urgency=.42, unblock=.76,
            risk=.30, seconds=15, downstream=1, attention_group="demo",
            fields_schema={"type":"object","properties":{"customer_id":{"type":"string"}},"required":["customer_id"]},
        ),
        HumanAsk(
            uri="human://auth", source="n8n", ref="flow-33",
            title="Reconnect the expired Salesforce credential",
            summary="The workflow needs a human to complete OAuth in the provider UI.",
            why_now="Lead sync is paused until authentication succeeds.", urgency=.58, unblock=.84,
            risk=.42, seconds=25, downstream=3, attention_group="demo",
        ),
    ]
    ids = [store.create(to_attention_request(a)).id for a in asks]

    # Historical explicit human decisions power the policy sandbox. They are
    # resolved records, not automation rules.
    for i in range(12):
        past = store.create(to_attention_request(HumanAsk(
            uri="human://approve", source="support-agent", ref=f"historic-refund-{i}",
            title="Refund under $20?", summary="Known customer; historically reviewed by a human.",
            urgency=.15, unblock=.25, risk=.16, seconds=9, downstream=0,
            policy_key="refund-under-20-known-customer", attention_group="demo",
        )))
        store.resolve(past.id, "demo-human", {"action":"approve", "values":{}, "comment":"historical demo decision"})

    # Low-value repeated work is deliberately kept out of the main queue.
    for i, amount in enumerate((8.90, 12.40, 14.20, 9.75)):
        store.create(to_attention_request(HumanAsk(
            uri="human://approve", source="support-agent", ref=f"small-refund-{i}",
            title=f"Refund ${amount:.2f}?", summary="Known customer; low-value refund.",
            urgency=.18, unblock=.30, risk=.18, seconds=9, downstream=0,
            batch_key="refunds-under-20", policy_key="refund-under-20-known-customer",
            attention_group="demo",
        )))
    return ids
