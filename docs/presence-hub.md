# Agent Presence Hub

Human Queue separates two concerns that should not be conflated:

1. **Presence** — continuously know what every connected agent session is doing.
2. **Intervention** — when a session truly needs a person, pause and resolve that exact boundary.

The Presence Hub is designed to run on one private-network host next to the Human Gateway.

> **Implementation status:** the normalized Presence registry, local connector lifecycle projection, CLI source registration, dashboard Fleet view, and MCP presence tools are implemented. The long-lived OpenClaw Gateway worker and Muse MSP worker are **not implemented or end-to-end validated yet**.

## Topology

```text
private LAN / Tailscale / trusted network

                Human Gateway
                     |
              Presence Registry
                     |
       +-------------+-------------+
       |             |             |
 OpenClaw worker  Muse worker  local editor hooks
   account A       account A    Codex / Claude / Cursor
 OpenClaw worker  Muse worker       / OpenCode
   account B       account B
       |             |             |
       +-------------+-------------+
                     |
              normalized state
                     |
        +------------+------------+
        |                         |
   Human Queue UI            MCP status tools
                               |
                         ask by conversation
```

Each connector is isolated as a worker. A broken provider, account, credential, or protocol upgrade should not take down the rest of the fleet.

## Identity model

A session is not globally identified by only a provider-native session id.

Human Queue uses:

```text
source_id = provider + account + gateway/host identity

(source_id, session_id)
```

This prevents two OpenClaw gateways, two Muse accounts, or two local profiles from colliding even if their native session ids happen to match.


Identity is only authoritative when the provider actually supplies enough provenance. Human Queue must not repair an ownerless event by guessing a default agent or bare `main` session.

OpenClaw [#126360](https://github.com/openclaw/openclaw/issues/126360) is a current example: some first-party/global paths still emit requests without an authoritative agent/session owner under explicit multi-agent ownership. Until the upstream runtime fixes those paths, a Presence connector should preserve them as unknown / unowned rather than presenting guessed state as authoritative.

## Normalized presence state

Every connector maps its native lifecycle onto:

```text
idle
running
waiting_human
waiting_external
completed
failed
offline
unknown
```

A normalized session can carry:

```json
{
  "source_id": "openclaw:work",
  "provider": "openclaw",
  "account": "work",
  "session_id": "session-key",
  "state": "running",
  "title": "Release API 2.4",
  "workspace": "/srv/api",
  "last_user": "Ship after CI is green.",
  "last_agent": "Checks passed; preparing deployment.",
  "current_action": "deploy",
  "waiting_reason": null,
  "progress": 0.8,
  "native": {
    "run_ids": ["run-123"]
  }
}
```

The `native` object is intentionally provider-specific. Product logic should depend on the normalized fields unless it is implementing a provider-specific deep link or resume operation.

## OpenClaw

OpenClaw is a strong candidate remote Presence Connector because its Gateway WebSocket exposes the control-plane data needed for sessions, runs, approvals and bounded history. The Human Queue live worker is still planned, not implemented.

A production worker should:

1. pair as an operator device;
2. request only the scopes it needs;
3. subscribe with `sessions.subscribe`;
4. merge `sessions.changed` events;
5. subscribe to selected session messages only when deeper context is needed;
6. use `hasActiveRun` / `activeRunIds` for current execution state;
7. subscribe to approval events when intervention routing is enabled;
8. resolve approvals through OpenClaw's native approval RPCs.

Do not infer liveness from the persisted session list alone. OpenClaw documents session rows separately from live channel connectivity and exposes live run facts on Gateway session projections.

Recommended scopes:

```text
operator.read       presence and bounded history
operator.approvals  pending approval projection + resolve
operator.write      only when Human Queue is allowed to send/steer sessions
```

## Muse Code

Muse Code should be connected through the Muse Session Protocol rather than screen scraping. The Human Queue Muse worker is still planned, not implemented.

Current Muse Code provides:

- `muse serve`;
- `@muse-code/sdk`;
- `session/list`;
- `session/read`;
- `session/resume`;
- running state + active turn id;
- permission decisions through the protocol;
- append-only per-session event logs.

The MSP surface is still Developer Preview, so its worker should be version-pinned and protocol drift should fail visibly.

The local JSONL event log is useful for recovery/audit, but it should not be the primary live-control contract when MSP is available.

## Local editor connectors

Codex, Claude Code, Cursor and OpenCode already emit native hooks/plugins into the Gateway.

Those lifecycle events automatically update Presence Hub state:

```text
SessionStart             -> idle
UserPromptSubmit         -> running
PreToolUse               -> running
PermissionRequest        -> waiting_human
beforeShellExecution     -> waiting_human
Stop / afterAgentResponse-> completed
SessionEnd               -> offline
```

Provider-native state remains available in the bounded connector registry.

## Third-party views

Slack, Telegram, mobile UI, email and other surfaces are **views of the Gateway**, not separate state stores.

```text
Presence Hub / Human Queue
          |
   +------+------+------+
   |      |      |      |
  Web  Telegram Slack  MCP
```

A third-party surface must never directly mutate an editor session. It resolves one canonical Human Queue request; the native connector performs the provider-specific resume.

## Query it conversationally

The Human Queue MCP server exposes:

```text
human_ask
human_presence_list
human_presence_summary
```

So an MCP-capable assistant can answer:

- “Which agents are still running?”
- “Which tasks are waiting for me?”
- “What is the Muse account doing?”
- “Did the OpenClaw release workflow finish?”
- “Show failed sessions across every account.”

without opening the dashboard.

## Register sources

Examples:

```bash
humanq source add openclaw work \
  --endpoint ws://openclaw-work.lan:18789 \
  --credential-env OPENCLAW_WORK_TOKEN

humanq source add openclaw personal \
  --endpoint ws://openclaw-personal.lan:18789 \
  --credential-env OPENCLAW_PERSONAL_TOKEN

humanq source add muse work-muse \
  --host localhost \
  --mode muse-msp

humanq source list
humanq presence
humanq presence --state waiting_human
```

Source records store **secret references**, not provider credentials, when possible. Workers should read tokens from environment variables, an OS secret store, or an external secret manager.

## Security boundary

The Presence Host should ideally be reachable only over the local network, Tailscale, WireGuard, or another trusted overlay.

Keep these layers separate:

```text
provider credentials
    stay inside connector worker

Gateway token
    authenticates local Human Queue API

channel token / hqc secret
    authenticates one human-facing projection
```

Do not copy all transcripts into the central database by default. Store bounded state and native locators; load deeper context on demand.

## What should run on one machine?

The recommended deployment unit is:

```text
human-presence-host
├── human-gateway
├── presence-registry
├── connector-supervisor
├── openclaw-work worker
├── openclaw-personal worker
├── muse-work worker
├── local editor hooks
├── telegram worker
└── slack socket-mode worker
```

This can be a home server, NAS, Mac mini, Linux box, VPS reachable through a private overlay, or a company-internal host.

The important property is not “one process.” It is **one private control plane with isolated connectors**.
