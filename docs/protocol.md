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

## Transport outcome states

A request should distinguish at least:

- `pending` — decision obligation still exists;
- `resolved` — an authorized answer was accepted;
- `expired` — the answer is no longer valid;
- `superseded` — newer state invalidated this request;
- `undeliverable` — no reachable human surface / transport existed;
- `unknown_owner` — source could not establish authoritative ownership.

`undeliverable` is not the same as “human did not respond.”

That distinction is important for systems where a runtime silently falls back to a prompt nobody is watching.

## Safety invariants

- Notification priority is not authorization.
- `batch` means “review together,” not “approve together automatically.”
- `defer` means “do not interrupt now,” not “discard the obligation.”
- An unreachable Human Queue must never fail open.
- An unauthorized response must not consume the pending request.
- An obsolete request must not remain approvable after supersession.
- A callback must bind back to the original request/session, not only to display text.

## Idempotency and supersession

Use `idempotency_key` when a retry represents the same boundary.

Use `supersession_key` when newer state invalidates an older unresolved request, such as a newer deployment replacing an older deployment approval.

## Runtime-local escalation is valid

Not every nested-agent boundary belongs in Human Queue.

If a leaf agent can safely escalate to a reachable parent without losing authority, provenance or required context, runtime-local escalation is usually simpler and should be preferred.

Human Queue is most useful only when the boundary survives that local simplification.

## Policy learning

Repeated human decisions may be replayed in the policy sandbox. A replay is evidence for a potential human-authored policy, not permission to silently enable one.
