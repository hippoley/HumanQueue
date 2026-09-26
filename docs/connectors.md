# Connector runtime

human:// treats every editor and channel as an adapter around one source of truth: the local Human Gateway.

> **Status:** Codex now has a packaged hook-process E2E for native `PermissionRequest` stdin → human:// → allow/deny stdout, but Codex host loading/trust/consumption is still a separate pending proof. Cursor is implemented and tested. Claude Code and OpenCode connectors are implemented, but real-host end-to-end validation is still pending. Slack and Telegram channel adapters are implemented; Slack workspace E2E is still pending. OpenClaw and Muse live workers are not implemented yet.

```text
Agent/editor native event
        |
        v
Native Connector
        |
        | ContextCapsule + NativeHandle
        v
Human Gateway
        |
        +---- Web UI
        +---- Slack / Telegram / other channel projections
        |
        v
human decision
        |
        v
Native Connector resume path
        |
        v
original conversation / tool call
```

## Codex

```bash
humanq connect codex
```

This installs user-level hooks in `~/.codex/hooks.json` for:

- `PermissionRequest`: blocks on human:// and returns Codex-native allow/deny JSON.
- `SessionStart`: registers the session.
- `UserPromptSubmit`: records the current turn and prompt.
- `Stop`: records the latest assistant message.
- `SessionEnd`: marks the session ended.

Codex exposes `session_id`, `transcript_path`, `cwd`, and the current model to command hooks. Turn hooks also expose `turn_id`. The connector stores a bounded context capsule and transcript pointer; it does not require parsing the whole transcript to function.

After installation, Codex requires you to review/trust the new non-managed hook definition once with `/hooks`. human:// deliberately does not infer that trust state from the hook file.

Check everything that can be established **before** the final native-host proof:

```bash
humanq verify codex
```

The verifier reports the Codex binary/version, Gateway health, installed PermissionRequest + observer hooks, whether the hook command still points at the Python runtime that installed it, and the remaining trust/real-host gap. It never marks the native host E2E as proven by configuration alone.

View observed sessions:

```bash
humanq sessions
```

### Approval round-trip

```text
Codex PermissionRequest
      |
      v
humanq connector hook codex-permission
      |
      v
POST /v1/human
      |
      v
human:// decision
      |
      v
hook stdout:
  behavior=allow | deny
      |
      v
Codex resumes the same tool call
```

If the Gateway is unavailable or the human:// wait times out, the connector emits no decision so Codex can fall back to its normal native approval UI instead of silently authorizing anything.

CI additionally runs the **built wheel** as a real hook subprocess for both allow and deny: JSON is written to stdin, the hook blocks on a real local Gateway request, a separate actor resolves the canonical boundary, and stdout is parsed as Codex PermissionRequest output. This proves the packaged hook process, not the final Codex host consumption.

## Cursor

```bash
humanq connect cursor
```

The Cursor connector observes native `sessionStart`, `sessionEnd`, `beforeSubmitPrompt`, and `afterAgentResponse` events so the Gateway can show the live conversation boundary without copying the whole transcript.

For native decision return, the connector intentionally gates only commands matching a conservative high-risk `beforeShellExecution` matcher, such as destructive deletes, pushes, infrastructure apply/delete commands, or state-changing HTTP calls. It does **not** claim to replace every Cursor approval surface.

Round-trip:

```text
Cursor beforeShellExecution
      |
      v
human:// request
      |
      v
approve / reject
      |
      v
Cursor hook stdout:
  permission=allow | deny
      |
      v
same generation continues
```

If human:// is unavailable or the wait expires, the connector returns `permission: ask`, handing control back to Cursor's own approval UI.


## Claude Code

```bash
humanq connect claude
```

The Claude Code connector installs native hooks for `PermissionRequest`, `SessionStart`, `UserPromptSubmit`, `Stop`, and `SessionEnd`.

At a normal foreground `PermissionRequest`, human:// can return Claude-native allow/deny output. If human:// is unavailable, it returns no structured decision so Claude Code keeps its native permission path.

**Important:** the connector is implemented, but real-host E2E remains pending. Public Claude Code reports also show that background `--bg` sessions can discard a hook decision and remain blocked. human:// therefore does not claim that “hook returned allow” proves the host resumed the background session.

## OpenCode

```bash
humanq connect opencode
```

human:// installs the V2 plugin from `integrations/opencode/human-queue.ts`.

The plugin evaluates only permission events that are already `ask`; configured native `allow` / `deny` semantics remain upstream-owned. When human:// cannot produce a decision, the permission remains `ask` rather than failing open.

The plugin is implemented and packaged, but real-host E2E remains pending.


## human.ask MCP bridge

Run:

```bash
humanq mcp serve
```

The stdio MCP server exposes one tool:

```text
human_ask
```

Use it for semantic human input where a native permission hook is the wrong abstraction:

- `human://clarify`
- `human://choose`
- `human://review`
- `human://edit`
- `human://auth`
- explicit `human://approve`

The tool blocks until the request resolves in the Human Gateway, then returns the decision to the exact MCP tool invocation, so the original agent conversation continues naturally.

Example generic MCP configuration:

```json
{
  "mcpServers": {
    "human": {
      "command": "humanq",
      "args": ["mcp", "serve"]
    }
  }
}
```

## Third-party human channels

Slack, Telegram, mobile push, email, or another UI are **projections**, not independent queues.

The current release includes a working generic signed-webhook projection:

```bash
humanq channel add webhook ops https://channel.example/human
humanq channel list
```

human:// generates a separate `hqc_...` channel secret. The Gateway sends a bounded request card to the configured URL with:

```text
X-Human-Channel: ops
X-Human-Channel-Signature: sha256=<HMAC>
```

The projection includes the request id, human:// URI, title, source, risk signals, actions, and a **bounded** context preview. It deliberately excludes the full transcript locator, arbitrary context blobs, and connector-private extras.

The receiver sends a decision to the request-specific callback included in the payload:

```text
POST /channels/ops/resolve/<request_id>
X-Human-Channel-Signature: sha256=<HMAC>
```

Example body:

```json
{
  "actor": "slack:user-123",
  "action": "approve",
  "values": {},
  "comment": "Reviewed from the ops channel"
}
```

The callback verifies the channel HMAC, resolves the one Gateway request, and rejects stale/already-resolved requests.

For webhook-backed workflows, human:// then attempts the configured resume callback. Delivery and semantic resume are distinct:

- HTTP 2xx means the callback transport accepted the request;
- `{"request_id":"attn_...","resumed":true}` with the exact same request id confirms semantic resume;
- otherwise the audit trail records `resume_delivered_unconfirmed`;
- transport failure records `resume_undeliverable`.

Native blocking connectors such as Codex / Claude / Cursor / MCP do not use this webhook path; their audit outcome is `resume_not_applicable`.

When the webhook receiver is remote, set `HUMAN_QUEUE_URL` to a URL that receiver can reach. Local transports such as Slack Socket Mode and Telegram long polling can avoid opening the Gateway directly.

### Telegram long-poll channel

Telegram is a concrete channel implementation that requires no public inbound Gateway URL:

```bash
export HUMAN_QUEUE_TELEGRAM_BOT_TOKEN="123:..."
export HUMAN_QUEUE_TELEGRAM_CHAT_ID="123456789"

humanq channel add telegram phone
humanq channel run phone
```

The Gateway sends each request to the configured chat with inline buttons. The local worker uses `getUpdates` long polling for `callback_query` events, verifies that the click came from the configured chat, resolves the canonical Gateway request, calls `answerCallbackQuery`, and removes the keyboard after a successful resolution.

A Telegram bot cannot use `getUpdates` while it has an outgoing webhook configured. The worker checks `getWebhookInfo` at startup and refuses to run in that conflicting state rather than silently reconfigure your bot.

Each channel adapter must:

1. render a bounded `ContextCapsule`;
2. retain the human:// `request_id`;
3. send human actions back to the Gateway;
4. mark stale/superseded projections read-only;
5. never directly drive the editor or agent.

The Native Connector alone owns the editor-specific resume mechanism.

This keeps all races, quorum, supersession, audit history and policy learning inside one Gateway.


### Slack Socket Mode channel

Slack is implemented as a local Socket Mode worker, so no public inbound Gateway port is required.

The worker renders bounded request context into Block Kit and resolves the canonical Gateway request when an authorized interaction arrives. human:// records the Slack user identity supplied by the authenticated Slack interaction path.

The channel implementation is present and tested at the adapter/security level, but real workspace end-to-end validation is still pending.
