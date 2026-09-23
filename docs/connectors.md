# Connector runtime

Human Queue treats every editor and channel as an adapter around one source of truth: the local Human Gateway.

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

- `PermissionRequest`: blocks on Human Queue and returns Codex-native allow/deny JSON.
- `SessionStart`: registers the session.
- `UserPromptSubmit`: records the current turn and prompt.
- `Stop`: records the latest assistant message.
- `SessionEnd`: marks the session ended.

Codex exposes `session_id`, `transcript_path`, `cwd`, and the current model to command hooks. Turn hooks also expose `turn_id`. The connector stores a bounded context capsule and transcript pointer; it does not require parsing the whole transcript to function.

After installation, Codex requires you to review/trust the new non-managed hook definition once with `/hooks`.

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
Human Queue decision
      |
      v
hook stdout:
  behavior=allow | deny
      |
      v
Codex resumes the same tool call
```

If the Gateway is unavailable or the Human Queue wait times out, the connector emits no decision so Codex can fall back to its normal native approval UI instead of silently authorizing anything.

## Cursor

```bash
humanq connect cursor
```

The Cursor connector observes native `sessionStart`, `sessionEnd`, `beforeSubmitPrompt`, and `afterAgentResponse` events so the Gateway can show the live conversation boundary without copying the whole transcript.

For native decision return, v0.5 intentionally gates only commands matching a conservative high-risk `beforeShellExecution` matcher, such as destructive deletes, pushes, infrastructure apply/delete commands, or state-changing HTTP calls. It does **not** claim to replace every Cursor approval surface.

Round-trip:

```text
Cursor beforeShellExecution
      |
      v
Human Queue request
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

If Human Queue is unavailable or the wait expires, the connector returns `permission: ask`, handing control back to Cursor's own approval UI.

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

v0.5 includes a working generic signed-webhook projection:

```bash
humanq channel add webhook ops https://channel.example/human
humanq channel list
```

Human Queue generates a separate `hqc_...` channel secret. The Gateway sends a bounded request card to the configured URL with:

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

The callback verifies the channel HMAC, resolves the one Gateway request, rejects stale/already-resolved requests, and then invokes the native connector resume path.

When the webhook receiver is remote, set `HUMAN_QUEUE_URL` to a URL that receiver can reach. Local-only transports such as a future Slack Socket Mode or Telegram long-poll adapter can avoid opening the Gateway directly.

Each channel adapter must:

1. render a bounded `ContextCapsule`;
2. retain the Human Queue `request_id`;
3. send human actions back to the Gateway;
4. mark stale/superseded projections read-only;
5. never directly drive the editor or agent.

The Native Connector alone owns the editor-specific resume mechanism.

This keeps all races, quorum, supersession, audit history and policy learning inside one Gateway.
