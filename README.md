<div align="center">

<br />

# human://

### The human control plane for autonomous systems.

**Machines can run anywhere. Human judgment needs an address.**

<br />

[Live Demo](https://hippoley.github.io/PAJ-Eval/human-queue/) &nbsp;&nbsp;·&nbsp;&nbsp; [Evidence](https://github.com/hippoley/HumanQueue/issues/1) &nbsp;&nbsp;·&nbsp;&nbsp; Protocol &nbsp;&nbsp;·&nbsp;&nbsp; Apache-2.0

<br />
<br />

<pre>
MACHINE  ──────────────────────►  HUMAN  ──────────────────────►  MACHINE
   yields control                    decides                       resumes
                                      │
                                  human://
</pre>

<br />

</div>

---

<br />

<table>
<tr>
<td width="33%" valign="top">

### ADDRESSABLE

Every human boundary gets a stable identity tied to the runtime, session and native request that created it.

</td>
<td width="33%" valign="top">

### TRANSPORTABLE

The same decision can surface on web, phone or chat without making the notification channel the source of truth.

</td>
<td width="33%" valign="top">

### EXACT

Resolve once. Kill stale decisions. Return the answer to the exact execution that yielded control.

</td>
</tr>
</table>

<br />

## Autonomy changes where humans belong.

Software used to wait for commands. Agents don't.

They plan, browse, write, deploy, delegate and keep working while you are somewhere else.

As autonomy scales, the scarce resource stops being machine execution.

> ### It becomes human judgment at the exact moment a machine cannot safely continue alone.

Today that judgment is fragmented across terminals, IDEs, browser tabs, permission dialogs, background sessions and chat threads.

**HumanQueue gives that boundary an address.**

<br />

```text
┌───────────────────────────────────────────────────────────────────────────┐
│                           AUTONOMOUS SYSTEMS                              │
│                                                                           │
│   Claude Code       Codex       OpenCode       MCP       your runtime     │
└────────┬──────────────┬────────────┬────────────┬────────────┬─────────────┘
         │              │            │            │            │
         └──────────────┴────────────┴─────┬──────┴────────────┘
                                           │
                                     HUMAN BOUNDARY
                                           │
                                           ▼
                              ┌────────────────────────┐
                              │        human://        │
                              │                        │
                              │ identity      context  │
                              │ authority     lifecycle│
                              │ resume        audit    │
                              └───────────┬────────────┘
                                          │
                         ┌────────────────┼────────────────┐
                         │                │                │
                         ▼                ▼                ▼
                        WEB             PHONE            CHAT
                         │                │                │
                         └────────────────┼────────────────┘
                                          │
                                          ▼
                                  HUMAN JUDGMENT
                                          │
                                          ▼
                                  EXACT RUN RESUMES
```

<br />

## One boundary. Any surface. Exact return.

A permission prompt is only the smallest form of the problem.

Once software acts asynchronously across sessions, runtimes and machines, a human boundary needs more than a button:

<table>
<tr>
<td><b>Identity</b><br/><sub>Who yielded control?</sub></td>
<td><b>Context</b><br/><sub>What does the human need?</sub></td>
<td><b>Authority</b><br/><sub>Who may decide?</sub></td>
</tr>
<tr>
<td><b>Lifecycle</b><br/><sub>Is this still pending?</sub></td>
<td><b>Resolution</b><br/><sub>Was it answered exactly once?</sub></td>
<td><b>Return</b><br/><sub>Which execution resumes?</sub></td>
</tr>
</table>

<br />

The invariant is deliberately small:

> ## A human decision must return to the exact machine state that requested it — once.

Slack is a surface. A phone is a surface. A web console is a surface.

**None of them owns the decision.**

<br />

```text
native request
     │
     ▼
stable identity ──► bounded context ──► authorized resolver
     │
     ▼
dedupe / supersede / expire
     │
     ▼
resolve once
     │
     ▼
native resume handle
     │
     ▼
exact execution
```

<br />

### Not another approval dashboard.

If one runtime already keeps every human boundary reachable and resumable, use its native UI.

HumanQueue starts where a single foreground session stops being enough: **background agents, nested workers, multiple runtimes, another device, another operator, or a fleet that needs one trustworthy human control surface.**

<br />

---

<br />

## Start here

```bash
git clone https://github.com/hippoley/HumanQueue.git
cd HumanQueue
pip install -e .
humanq demo
```

Open `http://127.0.0.1:7482`.

```text
YOU ARE BLOCKING 5 MACHINES.

#1  human://approve    Delete production cache keys?       ~8 sec
#2  human://approve    Deploy api@2.4.0 to production      ~6 sec
#3  human://auth       Reconnect Salesforce credential
```

The demo is not complete when a card appears. It is complete when the decision returns to the **correct waiting execution**.

For an empty self-hosted gateway:

```bash
humanq onboard
humanq gateway run
humanq dashboard
```

> Self-hosting, Docker, remote deployment and connector setup: [docs/self-host.md](docs/self-host.md)

<br />

## The protocol surface

Seven verbs cover the current human boundary:

| URI | Human contribution |
| --- | --- |
| `human://approve` | authorize or reject a consequential action |
| `human://review` | inspect and accept / change / reject output |
| `human://clarify` | supply missing information |
| `human://auth` | complete out-of-band authentication |
| `human://choose` | choose among explicit alternatives |
| `human://edit` | modify content before execution continues |
| `human://claim` | take ownership of a paused workflow |

Everything else — adapters, priority, batching, quorum, channels, callbacks — stays behind the boundary.

```python
from humanqueue import HumanQueue

human = HumanQueue()

decision = human.ask(
    "human://approve",
    source="release-agent",
    ref="deploy-2841",
    title="Deploy api@2.4.0 to production?",
    risk=.85,
    downstream=4,
    wait=True,
)

if decision["action"] == "approve":
    deploy()
```

For asynchronous systems, provide a resume target instead of blocking the worker. See [the protocol](docs/protocol.md).

<br />

## Reality, not roadmap

HumanQueue is being shaped against public failure reports.

| Observed boundary | What it teaches us |
| --- | --- |
| [Claude Code background permission](https://github.com/anthropics/claude-code/issues/88698) | seeing a request is not the same as having a reliable answer/resume path |
| [OpenCode nested subagent](https://github.com/anomalyco/opencode/issues/13715) | descendant asks need a reachable presentation path; native fixes may eliminate the need |
| [OpenClaw cross-channel workaround](https://github.com/openclaw/openclaw/issues/48529) | operators route decisions elsewhere when the originating surface cannot resolve them |
| [Hermes transport bypass](https://github.com/NousResearch/hermes-agent/issues/120859) | a decision can be delivered to a surface nobody is watching |
| [Vercel eve HITL authorization](https://github.com/vercel/eve/issues/1021) | durable pause also needs to know which human is authorized to resume it |

The [public evidence log](https://github.com/hippoley/HumanQueue/issues/1) records evidence **for and against** the project.

> **If native runtimes make every human boundary reachable and resumable, and cross-runtime operators do not need a shared control plane, HumanQueue should stay small.**

<br />

## Current reality

| Path | State |
| --- | --- |
| Gateway · web queue · SQLite ledger | **working** |
| Python · JavaScript · HTTP | **working** |
| Codex native permission round-trip | **working** |
| Cursor high-risk shell gate | **working** |
| MCP `human_ask` round-trip | **working** |
| Signed webhook projection | **working** |
| Telegram decision channel | **working** |
| Slack Socket Mode | implemented · workspace E2E pending |
| Claude Code | implemented · real-host E2E pending |
| OpenCode V2 | implemented · real-host E2E pending |
| Cross-account Presence registry | **working** |
| OpenClaw live Gateway worker | not implemented |
| Muse MSP live worker | not implemented |

No “supported” badge is granted for code that has not crossed its real host.

<br />

## Safety invariants

**Visibility is not permission.** Priority changes where a request appears, never whether it is approved.

**The surface is not the source of truth.** Slack, Telegram and the web UI project a canonical pending decision; they do not own it.

**Old decisions die.** Superseded, expired and resolved boundaries cannot revive stale execution.

**Failure falls back safely.** A connector that cannot reach HumanQueue returns to the runtime's native approval path rather than silently allowing an action.

**Automation is earned.** Historical decisions may produce a shadow policy candidate; HumanQueue never silently turns repeated approval into autonomy.

<br />

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
