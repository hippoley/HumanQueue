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

Each channel adapter must:

1. render a bounded `ContextCapsule`;
2. retain the Human Queue `request_id`;
3. send human actions back to `POST /v1/requests/{id}/resolve`;
4. mark stale/superseded projections read-only;
5. never directly drive the editor or agent.

The Native Connector alone owns the editor-specific resume mechanism.

This keeps all races, quorum, supersession, audit history and policy learning inside one Gateway.
