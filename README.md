# `human://`

### The control plane for human boundaries.

**Your agents got faster. You didn't.**

```text
identify exact waiting agent/session
        ↓
carry bounded decision context
        ↓
reach the right human
        ↓
record one authorized decision
        ↓
return it to the exact paused action
        ↓
prove exact-request resume when the runtime can acknowledge it
```

**[Live demo](https://hippoley.github.io/PAJ-Eval/human-queue/)** · **[Evidence log](https://github.com/hippoley/HumanQueue/issues/1)** · **[Claims & evidence](CLAIMS.md)** · **[Security model](SECURITY.md)** · **[Brand contract](docs/brand.md)** · Apache-2.0

A queue is one UI. The product is the boundary underneath it.

```text
Claude / worker-8
blocked 43s

Why it stopped
terraform wants to modify production state

What happened before
"...plan completed, 2 resources replace..."

[ inspect ] [ reject ] [ approve ]

decision returned
→ claude / session-91 / tool-14

exact waiting action resumed ✓
```

A local approval prompt works when the right human is already looking at the right session. The harder case is asynchronous: a nested or background agent pauses, the human is somewhere else, the request crosses a channel, and the answer still has to return to **one exact execution** without stale or duplicate authority.

That is a **HumanBoundary**:

```text
source / account / agent / session / request
                  │
                  ▼
             HumanBoundary
                  │
                  ├── bounded context
                  ├── authorized resolver
                  ├── expiry / supersession
                  ├── decision ledger
                  └── native resume handle
                  │
                  ▼
       web · phone · Slack · Telegram
                  │
                  ▼
        exact waiting action resumes
```

> If a runtime's native human-boundary path completely solves your workflow, use it. `human://` exists for the cases that survive the native fix.

## We tried to kill this idea

The project is deliberately tested against cases where it should **not** exist.

| Reality check | Result |
| --- | --- |
| OpenClaw Slack approval gap | fixed upstream by native Slack exec approvals → `human://` not needed for that local case |
| Mastra nested HITL | Supervisor Agent resolves the reported local topology → `human://` not needed there |
| Claude background workers | approval can exist while context/history/resume remain fragmented → boundary still under test |

The question is not “can we build another approval inbox?” It is:

> **After native runtimes fix their own approval UX, is there still a cross-session / cross-agent / cross-channel boundary that needs stable identity, human authority and exact resume?**

If the answer is usually no, this project should stay small.

## Brand and compatibility

| Layer | Name |
| --- | --- |
| Public brand / protocol language | **`human://`** |
| Core primitive | **`HumanBoundary`** |
| Product category | **human-boundary control plane** |
| CLI | `humanq` — retained for compatibility |
| Python distribution | `human-queue` — retained for compatibility |
| Python import | `humanqueue` — retained for compatibility |
| Legacy SDK class | `HumanQueue` — supported alias of `HumanBoundary` |
| Environment / local state | `HUMAN_QUEUE_*` and `~/.human-queue/` — retained for compatibility |

New examples prefer `HumanBoundary`; existing integrations do not need to rename anything.

## Run your own Human Gateway

human:// is **self-host first**. Hosted infrastructure is optional.

macOS / Linux / WSL:

```bash
curl -fsSL https://raw.githubusercontent.com/hippoley/HumanQueue/main/scripts/install.sh | bash
humanq gateway run
```

Then open your own queue:

```bash
humanq dashboard
```

First-run onboarding creates a private gateway token and persistent state under `~/.human-queue/`:

```text
~/.human-queue/
├── config.json
└── human-queue.db
```

Connect any agent or workflow to **your** gateway:

```bash
curl http://127.0.0.1:7482/v1/human \
  -H "Authorization: Bearer hq_xxx" \
  -H "Content-Type: application/json" \
  -d '{"uri":"human://approve","source":"my-agent","ref":"run-42","title":"Deploy to production?"}'
```

The request appears immediately in your local dashboard. Your decision can be polled by the caller or sent back through a signed resume webhook.

Useful local commands:

```bash
humanq onboard
humanq gateway run
humanq gateway status
humanq dashboard
humanq doctor
humanq token rotate

# real editor connectors
humanq connect codex
humanq connect cursor
humanq connect claude
humanq connect opencode
humanq sessions

# multi-account presence
humanq source add openclaw work --endpoint ws://openclaw-work.lan:18789 --credential-env OPENCLAW_WORK_TOKEN
humanq source add muse local-muse --mode muse-msp
humanq source list
humanq presence
humanq presence --state waiting_human

# semantic human input for MCP-capable agents
humanq mcp serve

# project requests into your own third-party channel
humanq channel add webhook ops https://your-channel.example/human

# no public inbound port required
humanq channel add telegram phone --bot-token "$BOT_TOKEN" --chat-id "$CHAT_ID"
humanq channel run phone

humanq channel add slack ops --app-token "$SLACK_APP_TOKEN" --bot-token "$SLACK_BOT_TOKEN" --channel-id "$SLACK_CHANNEL_ID"
humanq channel run ops

humanq channel list
```

Prefer containers?

```bash
git clone https://github.com/hippoley/HumanQueue.git
cd human-queue
bash scripts/docker/setup.sh
```

See [Self-hosting](docs/self-host.md) for Docker and remote/VPS deployment.


The unit of work is deliberately smaller than a workflow:

> **a machine has reached a boundary it cannot safely or correctly cross without a human contribution — and the answer must return to the exact thing that is waiting.**

---

## Try the seeded demo in 60 seconds

```bash
git clone https://github.com/hippoley/HumanQueue.git
cd human-queue
pip install -e .
humanq demo
```

Your browser opens at `http://127.0.0.1:7482` with a live demo containing:

- Codex waiting before a destructive shell action
- GitHub waiting at a production deployment gate
- a support agent asking about a $149 refund
- MCP asking for a missing identifier
- n8n waiting on OAuth
- several tiny refunds quietly moved into one batch
- historical human decisions replayed as a **policy candidate**, never auto-enabled

The first screen is intentionally simple:

```text
YOU ARE BLOCKING 5 MACHINES.

#1  human://approve
    Delete production cache keys?
    high consequence · unblocks 1 · ~8 sec

#2  human://approve
    Deploy api@2.4.0 to production
    unblocks 4 · ~6 sec

#3  human://auth
    Reconnect Salesforce credential
```

The goal is not to make you process approvals faster. The goal is to make fewer things deserve to interrupt you at all.

---

## The primitive

The public surface is deliberately tiny:

| URI | Meaning |
| --- | --- |
| `human://approve` | approve or reject a consequential action |
| `human://review` | inspect output and accept / change / reject |
| `human://clarify` | provide missing information |
| `human://auth` | complete an out-of-band authentication step |
| `human://choose` | choose among explicit alternatives |
| `human://edit` | modify content before the machine continues |
| `human://claim` | take ownership of a paused workflow |

Everything underneath — source-specific adapters, priority, batching, quorum, audit, callbacks — stays behind that boundary.

### Python

```python
from humanqueue import HumanBoundary

human = HumanBoundary()

decision = human.ask(
    "human://approve",
    source="my-agent",
    ref="run-42",
    title="Deploy to production?",
    why_now="CI passed and four downstream jobs are blocked.",
    risk=.85,
    downstream=4,
    seconds=8,
    wait=True,
)

if decision["action"] == "approve":
    deploy()
```

For long-running systems, do not block a worker: provide a `resume_url` and human:// returns the decision through a signed callback.

A successful HTTP callback proves transport delivery only. A target can optionally confirm semantic resume by returning:

```json
{"request_id":"attn_...","resumed":true}
```

human:// records that as `resume_confirmed`. A plain 2xx is retained as `resume_delivered_unconfirmed`; a transport failure is `resume_undeliverable`.

### JavaScript

```js
import { HumanBoundary } from './sdk/js/humanqueue.mjs';

const human = new HumanBoundary();
const request = await human.ask('human://review', {
  source: 'release-bot',
  ref: 'release-2841',
  title: 'Review generated release notes',
  seconds: 20,
});

const decision = await human.wait(request.id);
```

### Plain HTTP

```bash
curl -X POST http://127.0.0.1:7482/v1/human \
  -H 'content-type: application/json' \
  -d '{
    "uri":"human://approve",
    "source":"codex",
    "ref":"session-7",
    "title":"Run destructive command?",
    "why_now":"Execution is paused at the shell boundary.",
    "risk":0.98,
    "unblock":0.95,
    "seconds":8
  }'
```

See [`docs/protocol.md`](docs/protocol.md) for the protocol sketch.

---

## Why this can survive native approval UIs

A local approval prompt solves one important case: **the person is already inside the right runtime, looking at the right session**.

The harder cases appear when that assumption breaks:

```text
nested agent asks
        │
        ├── root UI cannot see it
        ├── operator is on another device
        ├── current channel cannot answer it
        ├── several sessions are waiting
        └── callback must prove which session + human it belongs to
        │
        ▼
HumanBoundary keeps provenance + resume identity
        │
        ▼
human decides from an authorized surface
        │
        ▼
native runtime resolves the exact request
```

That is why human:// keeps **source/account/session identity**, bounded decision context, authorization, supersession and native resume separate from presentation.

It can still rank, batch or defer requests, but **attention ordering never becomes implicit permission**. A high-risk item is made more visible, not more autonomous.

---

## What is implemented

### The fast path

- `POST /v1/human` for `human://…` requests
- Python client with blocking `wait()` for simple agents
- JavaScript client using standard `fetch`
- `humanq demo`, `humanq serve`, `humanq seed`
- live web inbox with Server-Sent Events
- source context rendered beside the decision
- webhook resume with optional HMAC SHA-256 signature

### The control plane underneath

- global priority ranking
- attention budgets: `interrupt / queue / batch / defer`
- deduplication via idempotency keys
- stale-request supersession
- `single / any_of / quorum / all_of` human routing
- durable SQLite ledger
- batch resolution
- audit events and decision history
- channel delivery evidence (`channel_delivered` / `channel_undeliverable`) without consuming the pending human obligation
- resume evidence that separates callback transport success from exact-request confirmation
- boundary-integrity metrics and dashboard counters for delivery / confirmation failure modes
- stable native identity required before idempotency or supersession can reuse a human boundary
- batch authorization is preflighted before any item is resolved, preventing 403-after-partial-approval states
- delegation frontier for repeated low-risk decisions
- **policy sandbox** that shadow-replays a proposed policy against historical human choices without enabling it

### Bidirectional connectors

These are different from normalization adapters: they observe a real editor session and know how to return the human decision to the native waiting point.

| Surface | Read session/context | Human → agent round-trip | Current scope |
| --- | --- | --- | --- |
| **Codex** | Native hooks: session, turn, prompt, final response, transcript locator | Native `PermissionRequest` allow/deny | Real connector |
| **Cursor** | Native hooks: session, prompt, final response, transcript locator | Native `beforeShellExecution` permission | High-risk shell gate only |
| **MCP** | Tool arguments supplied by the calling agent | `human_ask` tool result returns to the same MCP call | Real stdio bridge |
| **Generic webhook channel** | Receives bounded ContextCapsule | Signed action callback resolves the Gateway request | Real channel projection |
| **Telegram** | Receives bounded dialogue + decision buttons | Long-poll callback resolves local Gateway | Real outbound-only channel |
| **Slack** | Receives bounded Block Kit card | Socket Mode action resolves local Gateway | Channel implemented; workspace E2E still pending |
| **Claude Code** | Native hooks: session/prompt/stop + transcript locator | Native PermissionRequest allow/deny | Connector implemented; real-host E2E still pending |
| **OpenCode V2** | Prompt + session context via plugin API | Permission evaluate hook mutates allow/deny | Plugin implemented; real-host E2E still pending |
| **OpenClaw Gateway** | Gateway WS sessions/active-run/approval APIs | Native approval RPCs | Presence worker design ready; live worker next |
| **Muse Code** | MSP session/list/read + event-sourced sessions | MSP approval/decide | Presence worker design ready; live worker next |

```bash
humanq connect codex
humanq connect cursor
humanq sessions
```

A connector stores a bounded `ContextCapsule` in the Gateway. Full editor transcripts are not copied into the queue by default; the transcript path is retained only as an on-demand locator.

If a native editor connector cannot reach human:// or times out, it falls back to the editor's native approval path rather than silently allowing the action.

See [Connector runtime](docs/connectors.md).

### Telegram: approve from your phone

Telegram is the first concrete third-party channel built on the projection model. It uses Bot API long polling, so a self-hosted Gateway can stay behind NAT/firewall without exposing an inbound HTTP endpoint.

```bash
export HUMAN_QUEUE_TELEGRAM_BOT_TOKEN="..."
export HUMAN_QUEUE_TELEGRAM_CHAT_ID="..."

humanq channel add telegram phone
humanq channel run phone
```

New human:// requests are sent to that chat with inline decision buttons. The worker accepts callbacks only from the configured chat, resolves the canonical Gateway request, clears the buttons after a successful decision, and the native editor connector resumes the original waiting workflow.

The Telegram bot token stays in the local `~/.human-queue/config.json` file; `humanq channel list --json` redacts it.

### Normalization adapters

- generic JSON
- A2A `input-required` / auth-style states
- MCP-shaped elicitation payloads
- OpenAI-style tool interruption payloads
- GitHub deployment approval payloads

These adapters normalize external events into human://. They are not the same as a native bidirectional editor integration.

---

## Agent Presence Hub

human:// now separates **continuous fleet presence** from **human intervention**.

```text
OpenClaw work account ─┐
OpenClaw personal ─────┤
Muse work ─────────────┤
Codex / Claude / Cursor├──► Presence Hub ───► normalized session state
OpenCode ──────────────┘            │
                                    ├──► human:// when a person is needed
                                    └──► MCP status tools for conversational queries
```

The identity boundary is `source_id + session_id`, so two accounts or gateways can safely expose identical native session IDs.

Normalized states:

```text
idle · running · waiting_human · waiting_external · completed · failed · offline · unknown
```

The same state is queryable through the dashboard/API or conversationally through:

```text
human_presence_list
human_presence_summary
```

So an MCP-capable assistant can answer “which agents are still running?” or “what is waiting for me?” without opening each editor.

OpenClaw is the strongest future remote worker target because its Gateway WebSocket exposes live session subscriptions, active-run state and approval RPCs. Muse Code now exposes `muse serve` / MSP with session listing, read/resume and permission decisions. The Presence Hub source/account registry is implemented; the long-lived OpenClaw and Muse supervisor workers are the next step.

See [Agent Presence Hub](docs/presence-hub.md).

---

## The part that matters long-term

A queue is only phase one.

```text
1. WHERE DO I NEED TO LOOK?
                  ↓
2. WHAT DESERVES MY ATTENTION?
                  ↓
3. WHY DOES THIS STILL NEED ME?
```

Every repeated interruption is evidence that the boundary may belong in a policy instead.

human:// therefore keeps explicit `policy_key` histories and can replay a candidate policy in shadow mode:

```text
refund-under-20-known-customer

historical decisions       48
would match humans          47
conflicts                    1
observed attention          7m 12s
mode               shadow_only
policy enabled           false
```

The system can show the opportunity. A person still decides whether that policy should exist.

---

## Run it

### Local

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
humanq demo
```

Run an empty server instead:

```bash
humanq serve --host 0.0.0.0 --port 7482
```

The default database lives at `~/.human-queue/human-queue.db`. Override it with `HUMAN_QUEUE_DB`.

### Docker

```bash
docker compose up --build
```

Open `http://127.0.0.1:7482`.

---

## API map

```text
POST /v1/human                         small human:// envelope
POST /v1/requests                      full canonical request
POST /v1/import                        adapter normalization
GET  /v1/queue                         active global queue
GET  /v1/batches                       batched human work
GET  /v1/requests/{id}                 request + event history
POST /v1/requests/{id}/resolve         human decision
POST /v1/requests/{id}/claim           ownership
GET  /v1/metrics                       attention metrics
GET  /v1/delegation-frontier           policy candidates
GET  /v1/policy-sandbox/{policy_key}   shadow replay
GET  /v1/events/stream                 live queue changes
POST /v1/connectors/events             native connector observations
GET  /v1/connectors/sessions           observed agent/editor sessions
GET  /v1/connectors/sessions/{p}/{id}  bounded session context + recent events
POST /v1/presence/sources/{source_id}  register one provider/account source
GET  /v1/presence/sources              list connected source accounts
POST /v1/presence/sessions             ingest normalized session presence
GET  /v1/presence/sessions             fleet state across all accounts
POST /channels/{name}/resolve/{id}     signed third-party channel decision
```

FastAPI also exposes interactive API docs at `/docs`.

---

## Safety model

`human://` schedules attention. It does **not** silently take consequential decisions away from people.

- priority controls visibility, not approval
- defer means “do not interrupt now,” not “discard”
- batch means “review together,” not “approve together automatically”
- policy suggestions remain suggestions
- sandbox replay never enables a policy
- stale requests can be superseded so people do not approve obsolete state
- signed resume callbacks preserve the source→human→source chain

Production use still needs hardened identity, inbound signature verification, secret management, tenant isolation, escalation workers, and real bidirectional connectors.

---

## Tests

```bash
pytest -q
# 51 passed
```

The current suite covers queue semantics, gateway authentication, Codex/Cursor/Claude native round-trips, OpenCode plugin packaging, connector session tracking, multi-account Presence Hub state, MCP fleet-status tools, signed webhook projection, Telegram and Slack channel rendering/security, channel-delivery failure auditing, duplicate-resolution protection, policy replay, batching, supersession, quorum, and the cross-platform demo seed.

---

## Contributing

Do not start with “support another logo.”

Start with one real boundary that failed in a real workflow:

```text
runtime + version
what was waiting
which session actually owned the request
where the human expected to answer
whether that surface was reachable
whether a native programmatic resolve path existed
what workaround you used
```

Then ask:

> **Where does this machine genuinely stop because only a human can safely move it forward?**

If the answer is “the runtime just routed its own prompt incorrectly,” fix the runtime upstream. That is a successful outcome for this project too.

If the boundary survives the local fix — across sessions, accounts, devices or channels — add it to the [evidence thread](https://github.com/hippoley/HumanQueue/issues/1) before proposing a connector.

The best contribution is evidence that changes the architecture, including evidence that removes something from the roadmap.

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

Apache-2.0.
