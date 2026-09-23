# `human://` protocol sketch

`human://` is a deliberately small application-level convention for one event:

> autonomous software cannot safely or correctly continue without a human contribution.

It is not a transport protocol. HTTP, MCP, A2A, webhooks, queues and local IPC can all carry the same semantic request.

## Verbs

| URI | Human contribution |
| --- | --- |
| `human://approve` | approve or reject a consequential action |
| `human://review` | inspect output and accept, reject, or request changes |
| `human://clarify` | provide missing structured or free-form information |
| `human://auth` | complete an out-of-band authentication step |
| `human://choose` | choose among explicit alternatives |
| `human://edit` | edit machine-produced content before continuation |
| `human://claim` | take ownership of a paused workflow |

## Minimal envelope

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

The server expands this small envelope into its richer canonical request model. Ranking may decide **when and where to surface** the request; it must never be interpreted as permission to take the human's decision automatically.

## Lifecycle

```text
machine
  │
  ├─ emits human://…
  ▼
queued / batched / deferred / interrupt
  │
  ▼
human contribution
  │
  ├─ resolution returned by poll, callback, or adapter
  ▼
machine resumes
```

## Safety invariant

A request may be suppressed as a notification, but not discarded as a decision obligation. `batch` and `defer` affect attention scheduling only. They do not mean approval.

## Idempotency and supersession

Use `idempotency_key` when a retry represents the same interrupt. Use `supersession_key` when a new state invalidates an older unresolved request, such as a newer production deployment replacing an older deployment approval.

## Policy learning

Repeated human decisions may be replayed in the policy sandbox. A replay is evidence for a potential human-authored policy, not permission to silently enable one.
