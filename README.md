# `human://`

### One queue for everything that needs a human.

**[Try the live interactive demo →](https://hippoley.github.io/human-queue/)**

Agents can run in parallel. Workflows can run for hours. CI can deploy itself. The expensive part is what happens when any of them reaches a boundary that still needs **you**.

`human://` turns every machine→human interruption into one small primitive, ranks only what deserves attention now, lets a person decide with enough context, and resumes the machine that was waiting.

```text
Codex      ─┐
Claude      │    human://approve
GitHub      │    human://review
MCP         ├──► human://clarify ───► HUMAN QUEUE ───► decision ───► resume
A2A         │    human://auth
n8n         │    human://choose
Your app   ─┘    human://edit
```

**Not another agent dashboard. Not another approval product.**

## Run your own Human Gateway

Human Queue is **self-host first**. Hosted infrastructure is optional.

macOS / Linux / WSL:

```bash
curl -fsSL https://raw.githubusercontent.com/hippoley/human-queue/main/scripts/install.sh | bash
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
humanq sessions

# semantic human input for MCP-capable agents
humanq mcp serve

# project requests into your own third-party channel
humanq channel add webhook ops https://your-channel.example/human\n\n# no public inbound port required\nhumanq channel add telegram phone --bot-token "$BOT_TOKEN" --chat-id "$CHAT_ID"\nhumanq channel run phone\n\nhumanq channel list
```

Prefer containers?

```bash
git clone https://github.com/hippoley/human-queue.git
cd human-queue
bash scripts/docker/setup.sh
```

See [Self-hosting](docs/self-host.md) for Docker and remote/VPS deployment.


The unit of work is simply:

> software cannot safely or correctly continue until a human contributes something small.

---

## Try the seeded demo in 60 seconds

```bash
git clone https://github.com/hippoley/human-queue.git
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
from humanqueue import HumanQueue

human = HumanQueue()

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

For long-running systems, do not block a worker: provide a `resume_url` and Human Queue returns the decision through a signed callback.

### JavaScript

```js
import { HumanQueue } from './sdk/js/humanqueue.mjs';

const human = new HumanQueue();
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

## Why a global queue is different

Without a shared attention layer:

```text
Codex   → open Codex
GitHub  → open GitHub
Jira    → open Jira
Slack   → open Slack
n8n     → open n8n
email   → open email
```

With `human://`:

```text
all machines
    │
    ▼
what actually needs a person?
    │
    ├── interrupt now
    ├── normal queue
    ├── batch similar decisions
    └── defer low-value noise
    │
    ▼
human decides
    │
    ▼
original machine resumes
```

**Attention ordering never becomes implicit permission.** A high-risk item is made more visible, not more autonomous.

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
- delegation frontier for repeated low-risk decisions
- **policy sandbox** that shadow-replays a proposed policy against historical human choices without enabling it

### Bidirectional connectors

These are different from normalization adapters: they observe a real editor session and know how to return the human decision to the native waiting point.

| Surface | Read session/context | Human → agent round-trip | Current scope |
| --- | --- | --- | --- |
| **Codex** | Native hooks: session, turn, prompt, final response, transcript locator | Native `PermissionRequest` allow/deny | Real connector |
| **Cursor** | Native hooks: session, prompt, final response, transcript locator | Native `beforeShellExecution` permission | High-risk shell gate only |
| **MCP** | Tool arguments supplied by the calling agent | `human_ask` tool result returns to the same MCP call | Real stdio bridge |
| **Generic webhook channel** | Receives bounded ContextCapsule | Signed action callback resolves the Gateway request | Real channel projection |\n| **Telegram** | Receives bounded dialogue + decision buttons | Long-poll callback resolves local Gateway | Real outbound-only channel |
| Claude Code / OpenCode | — | — | Not implemented yet |

```bash
humanq connect codex
humanq connect cursor
humanq sessions
```

A connector stores a bounded `ContextCapsule` in the Gateway. Full editor transcripts are not copied into the queue by default; the transcript path is retained only as an on-demand locator.

If a native editor connector cannot reach Human Queue or times out, it falls back to the editor's native approval path rather than silently allowing the action.

See [Connector runtime](docs/connectors.md).

### Telegram: approve from your phone

Telegram is the first concrete third-party channel built on the projection model. It uses Bot API long polling, so a self-hosted Gateway can stay behind NAT/firewall without exposing an inbound HTTP endpoint.

```bash
export HUMAN_QUEUE_TELEGRAM_BOT_TOKEN="..."
export HUMAN_QUEUE_TELEGRAM_CHAT_ID="..."

humanq channel add telegram phone
humanq channel run phone
```

New Human Queue requests are sent to that chat with inline decision buttons. The worker accepts callbacks only from the configured chat, resolves the canonical Gateway request, clears the buttons after a successful decision, and the native editor connector resumes the original waiting workflow.

The Telegram bot token stays in the local `~/.human-queue/config.json` file; `humanq channel list --json` redacts it.

### Normalization adapters

- generic JSON
- A2A `input-required` / auth-style states
- MCP-shaped elicitation payloads
- OpenAI-style tool interruption payloads
- GitHub deployment approval payloads

These adapters normalize external events into Human Queue. They are not the same as a native bidirectional editor integration.

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

Human Queue therefore keeps explicit `policy_key` histories and can replay a candidate policy in shadow mode:

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
# 25 passed
```

The current suite covers queue semantics, gateway authentication, Codex and Cursor native decision round-trips, connector session tracking, MCP initialize/list/call behavior, signed bounded channel projection, policy replay, batching, supersession, quorum, and the cross-platform demo seed.

---

## Contributing

The best connector contribution is not “support another logo.” It is a clean answer to:

> **Where does this machine genuinely stop because only a human can safely move it forward?**

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

Apache-2.0.
