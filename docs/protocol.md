# `human://` protocol sketch

`human://` is an application-level convention for a **human boundary**:

> software has reached a specific action or decision that cannot safely or correctly continue without a human contribution, and that contribution must return to the exact thing that is waiting.

It is not a transport protocol and it is not an approval UI. HTTP, MCP, A2A, webhooks, local IPC, Slack, Telegram or a native runtime can all carry the same boundary.

## The boundary identity

The verb is not the most important field. Provenance is.

A durable boundary should be attributable to:

```text
provider
account / gateway
agent
session
request / action
```

and should carry, where available:

- bounded decision context;
- authorized resolver identity/policy;
- expiry;
- supersession identity;
- native resume handle;
- audit outcome.

If ownership is ambiguous, the protocol should preserve that ambiguity. It must not invent a default owner merely to make the request routable.

## Human contribution verbs

| URI | Human contribution |
| --- | --- |
| `human://approve` | approve or reject a consequential action |
| `human://review` | inspect output and accept, reject, or request changes |
| `human://clarify` | provide missing structured or free-form information |
| `human://auth` | complete an out-of-band authentication step |
| `human://choose` | choose among explicit alternatives |
| `human://edit` | edit machine-produced content before continuation |
| `human://claim` | take ownership of a paused workflow |

These verbs describe **what the person contributes**. They do not define how the original runtime is resumed.

## Minimal envelope

A minimal app-created request can still be small:

```json
{
  "uri": "human://approve",
  "source": "codex",
  "ref": "session-7",
  "title": "Run destructive command?",
  "why_now": "Execution is paused at the shell boundary.",
  "risk": 0.98,
  "unblock": 0.95,
  "seconds": 8,
  "downstream": 1
}
```

But a native connector should retain richer provenance internally when the runtime exposes it.

## Lifecycle

```text
native runtime / workflow
        │
        ├─ reaches human boundary
        ▼
identity + provenance captured
        │
        ├─ surface now
        ├─ queue
        ├─ batch
        └─ defer notification
        ▼
authorized human contributes
        │
        ▼
native/programmatic resolve
        │
        ▼
exact waiting action resumes
```

The distinction between **presentation** and **resolution** is intentional. Showing a request in Slack, a web queue or a phone is not enough; the system must retain a trustworthy route back to the exact suspended action.

## Transport outcome semantics

The current implementation exposes request lifecycle states such as `pending`, `claimed`, `resolved`, `expired`, `cancelled` and `superseded`.

Two additional failure semantics are **protocol requirements we have evidence for, but are not yet first-class RequestStatus values**:

- **undeliverable** — no reachable human surface / transport existed;
- **unknown owner** — the source could not establish authoritative ownership.

Until those are represented explicitly in the canonical model, integrations should preserve the evidence in connector/presence context rather than pretending the human ignored the request or guessing an owner.

`undeliverable` is not the same as “human did not respond.”

That distinction is important for systems where a runtime silently falls back to a prompt nobody is watching.

## Resume receipts

The signed callback always includes the canonical `request_id` together with source provenance and the human resolution.

HTTP success alone means only **the callback transport accepted the request**. It does not prove the intended suspended action resumed.

A callback target may provide a stronger receipt:

```json
{
  "request_id": "attn_...",
  "resumed": true
}
```

human:// treats this as semantic confirmation only when the receipt's `request_id` exactly matches the canonical request being resumed.

The resulting audit semantics are:

- `resume_queued` — a webhook-backed resume obligation was durably committed with the human decision;
- `resume_confirmed` — transport succeeded and the target explicitly acknowledged the same request;
- `resume_delivered_unconfirmed` — transport succeeded but no exact-request receipt was returned;
- `resume_undeliverable` — the callback could not be delivered.

Webhook resume uses a durable SQLite outbox. The outbox record is created in the **same transaction** that finalizes the human decision, so a Gateway crash after `resolved` but before network delivery does not erase the obligation. A restarted Gateway leases and retries due outbox work.

Crash recovery makes webhook delivery **at least once**, not exactly once. Every callback carries the canonical request id in the body, `X-Attention-Request-Id`, and the standard `Idempotency-Key` header. A webhook receiver that performs consequential side effects **must deduplicate by that request id** and return the same exact-request receipt on retries.

This also covers the harder failure window where the receiver already applied the side effect but the Gateway crashed before persisting `resume_confirmed`: after the lease expires the transport may be repeated, while an idempotent receiver applies the logical action once.

This avoids treating “HTTP 200” as proof that the correct waiting session consumed the decision and avoids pretending distributed HTTP transport can guarantee exactly-once execution.

## Decision provenance and responder identity

A human boundary must distinguish **who/what produced an outcome** from the outcome itself.

By default, `RoutePolicy.required_actor_kind = "human"`. The normal resolve path accepts an explicit provenance class:

```json
{
  "actor": "alice",
  "actor_kind": "human",
  "action": "approve"
}
```

`actor_kind` may be `human`, `system`, `policy`, or `service`. A request that requires human authority rejects non-human provenance on the resolution path.

Machine lifecycle events use a separate endpoint and terminal state:

```json
POST /v1/requests/attn_.../outcome
{
  "actor": "timeout-worker",
  "actor_kind": "system",
  "outcome": "expired",
  "reason": "deadline elapsed"
}
```

That transition leaves `resolution = null`. Timeout, cancellation, scheduler action, or policy action therefore cannot be encoded as if a human answered.

A boundary may explicitly set `required_actor_kind = "any"` when a deployment intentionally permits policy/service resolution. That is opt-in.

human:// also enforces responder policy with `route.actors`, but those actor strings and actor-kind assertions are only as trustworthy as the surface that supplies them.

Current trust model:

- Slack Socket Mode derives the actor from Slack's authenticated interaction payload;
- Telegram long polling derives the actor from Telegram's callback user and verifies the configured chat;
- signed generic webhooks trust the configured channel secret to assert the actor;
- direct Gateway API calls are trusted at the Gateway bearer-token boundary.

human:// does **not** yet provide an independent per-human identity provider or IAM layer. A value such as `slack:U123` is therefore an authenticated channel identity only when it came through the trusted Slack connector path; the same string supplied by an administrator holding the Gateway token is still an administrator assertion.

This is intentionally documented as a boundary rather than hidden behind the phrase “authorized resolver.” If a deployment needs CODEOWNER-, role-, organization- or directory-backed authorization, that policy must currently be enforced by the trusted channel/application or by a future responder-authorization layer.

## Native blocking connectors

Codex, Claude Code, Cursor and MCP-style blocking calls can wait synchronously for human:// and return the human decision directly through the host's native hook/tool call.

For these paths, `resume.mode = none` is expected. The audit event is `resume_not_applicable`, not `resume_undeliverable`.

A native hook returning a decision proves only that human:// returned a decision to the connector. Whether the host runtime actually consumes that decision is still runtime-specific evidence; this is especially important for background-session bugs where a host may discard a hook result.

## Safety invariants

- Notification priority is not authorization.
- A system event, timeout, policy action, or service callback is not a human answer.
- Source `Stop`, turn completion, or session teardown does not implicitly resolve a still-pending human boundary; clearing it requires an explicit answered/dismissed/expired/cancelled/replaced transition.
- A human-only boundary must reject non-human `actor_kind` on the resolution path.
- `batch` means “review together,” not “approve together automatically.”
- `defer` means “do not interrupt now,” not “discard the obligation.”
- An unreachable human:// must never fail open.
- An unauthorized response must not consume the pending request.
- An obsolete request must not remain approvable after supersession.
- A callback must bind back to the original request/session, not only to display text.
- Webhook receivers must treat canonical `request_id` / `Idempotency-Key` as a deduplication key because crash recovery may retry transport.

## Idempotency and supersession

Use `idempotency_key` only when the source exposes enough stable native identity to prove that a retry represents the same boundary.

Use `supersession_key` only when newer state can be tied to the same authoritative native worldline, such as a newer deployment replacing an older deployment approval.

> **No stable identity → no idempotency, no supersession.**

Fallback labels such as `unknown`, `run`, `main`, or `unidentified` are display/debug values, never proof that two requests are the same request. When provenance is incomplete, human:// prefers duplicate visible requests over reusing or superseding the wrong human decision.

## Runtime-local escalation is valid

Not every nested-agent boundary belongs in human://.

If a leaf agent can safely escalate to a reachable parent without losing authority, provenance or required context, runtime-local escalation is usually simpler and should be preferred.

human:// is most useful only when the boundary survives that local simplification.

## Policy learning

Repeated human decisions may be replayed in the policy sandbox. A replay is evidence for a potential human-authored policy, not permission to silently enable one.
