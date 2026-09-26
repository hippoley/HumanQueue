# Contributing

human:// is developed **evidence first**.

Do not start with “please support another agent/runtime/channel.” Start with a real workflow where a human boundary failed or became operationally expensive.

## Bring a boundary, not a logo

A useful report answers:

1. **Runtime + version** — what actually ran?
2. **Topology** — foreground, background, nested agent, remote session, shared channel, etc.
3. **Owner** — which account / agent / session actually owned the request?
4. **Boundary** — what exactly could not continue without a person?
5. **Expected surface** — where did the human expect to answer?
6. **Actual surface** — where did the request really appear, if anywhere?
7. **Native resolve path** — could software programmatically resolve the exact waiting request?
8. **Resume identity** — after the answer, how was the exact waiting session/action resumed?
9. **Workaround** — polling, TUI scraping, Telegram, Slack, parent escalation, auto-deny, etc.
10. **What would make human:// unnecessary?** — name the local/runtime fix if one exists.

If the problem disappears after a runtime-local fix, upstream that fix. That is a successful result for this project.

## Before proposing a connector

A connector is justified only when the human boundary survives the native runtime fix.

Good reasons include:

- the human is routinely away from the foreground runtime;
- several sessions/accounts/runtimes can wait at once;
- the originating channel cannot safely resolve the request;
- responder authorization matters independently from request-time policy;
- provenance must survive a cross-channel hop;
- the decision must resume one exact suspended action.

Bad reasons include:

- the native TUI forgot to render a prompt;
- a child agent can safely escalate to a reachable parent;
- the runtime already exposes a complete remote/mobile approval path;
- the integration only adds another logo without a distinct boundary.

## The contract we care about

A contribution should preserve this chain:

```text
provider
+ account / gateway
+ agent
+ session
+ request
        ↓
bounded decision context
+ authorized resolver
+ expiry / supersession
+ native resume handle
        ↓
human contribution
        ↓
exact waiting action resumes
```

Unknown ownership stays unknown. Do not guess `main`, a default account, or a parent session just to make a request routable.

## Pull requests

Keep provider-specific semantics inside connectors/adapters. The canonical model should remain provider-neutral.

Ranking, batching and notification priority may change **when or where** a request is surfaced. They must never become implicit permission.

For connector PRs, include at least one failure-path test proving that an unreachable human:// does **not** silently allow a consequential action.

Run:

```bash
pytest -q
```

before opening a PR.

For research/evidence contributions, code is optional. A well-reproduced counterexample that removes something from the roadmap is a valuable contribution.
