# Claims and evidence

human:// separates **implemented code**, **verified behavior**, **real-host validation**, and **product hypotheses**.

A green checkbox here means only what the evidence column says it means.

| Claim | Status | Evidence | Current limit |
| --- | --- | --- | --- |
| Canonical local blocking path works from the packaged wheel | **packaged-wheel E2E verified** | clean venv installs built wheel, onboards, starts Gateway, blocks in `HumanBoundary.ask(wait=True)`, resolves the same request, then proves the original caller continues | Does not prove an external provider runtime consumes the decision |
| Gateway API is bearer-token protected | **verified in CI** | gateway auth tests | Gateway token is an administrative credential, not per-human IAM |
| Channel delivery failures are observable | **verified in CI** | `channel_delivered` / `channel_undeliverable` audit tests | Delivery evidence does not change request lifecycle yet |
| Webhook resume transport is distinct from semantic resume | **verified in CI** | exact-request receipt + transport-error tests | Semantic confirmation is opt-in for webhook targets |
| Native blocking waits are not misreported as webhook failures | **verified in CI** | `resume_not_applicable` regression test | Host runtime consumption remains provider-specific |
| Missing native identity cannot dedupe or supersede decisions | **verified in CI** | A2A/OpenAI + Codex/Claude/Cursor identity tests | Duplicate visible requests are intentionally preferred to wrong reuse |
| Blank Presence / connector provenance is rejected | **verified in CI** | API boundary regression test | This does not make upstream provider ownership authoritative |
| Boundary-integrity counters are observable | **verified in CI** | `integrity_last_24h` + dashboard tests | Metrics measure human:// events, not every provider-internal failure |
| MCP `human_ask` blocks and returns one structured decision | **verified in CI** | MCP protocol tests | Depends on the MCP host keeping the tool invocation alive |
| Codex native PermissionRequest hook process | **packaged-hook E2E verified; real-host pending** | clean-wheel subprocess consumes PermissionRequest stdin, blocks on the real Gateway, preserves native session/turn identity, and emits Codex-shaped allow + deny stdout | Codex binary trust/loading and host consumption of the hook decision are not yet proven |
| Cursor high-risk shell gate | **implemented + tested** | allow/fallback tests | Intentionally not a universal Cursor approval replacement |
| Claude Code PermissionRequest adapter | **implemented; real-host E2E pending** | connector tests + packaged runtime | Claude `--bg` has public evidence that a returned hook decision may not resume the background session |
| OpenCode V2 permission plugin | **implemented; real-host E2E pending** | plugin packaging / source tests | Upstream permission semantics remain authoritative |
| Telegram long-poll decision channel | **implemented + adapter tested** | callback auth / bounded-context tests | Production bot/network E2E is deployment-specific |
| Slack Socket Mode decision channel | **implemented; workspace E2E pending** | rendering/security adapter tests | Real Slack workspace round-trip still needs external validation |
| Generic signed webhook channel | **implemented + tested** | HMAC / bounded projection / duplicate-resolution tests | Receiver identity is trusted through the configured channel secret |
| Presence registry + Fleet view + MCP presence tools | **implemented + tested** | Presence / dashboard / MCP tests | Aggregated state is only as authoritative as provider provenance |
| OpenClaw live Presence worker | **not implemented** | architecture only | Do not infer support from source registration |
| Muse MSP live Presence worker | **not implemented** | architecture only | MSP is also a provider-preview surface |
| OpenClaw Slack approval gap requires human:// | **falsified for that local case** | OpenClaw PR #58155 merged native Slack exec approvals | Historical workaround should not be cited as a current need |
| Multi-agent HITL requires an external human:// | **falsified for at least one local case** | Mastra #18766 resolved the reported 8-agent nested-HITL shape with Supervisor Agent | A correct runtime-local supervisor can preserve suspend/resume without a cross-runtime control plane |
| Cross-runtime Presence Hub is independently valuable after native fixes | **hypothesis** | no direct operator adoption evidence yet | OpenClaw ownership bugs are provenance evidence, not validation of a separate Presence product |
| One cross-runtime HumanBoundary contract is useful beyond native UIs | **hypothesis under test** | public evidence thread + external probes | Needs real operator adoption / integration evidence |

## Evidence hierarchy

human:// uses this order when deciding whether to build:

1. **Real incident / ugly workaround**
2. **Independent reproduction**
3. **Upstream fix or maintainer response**
4. **External operator says the problem survives the native fix**
5. **External integration / adoption**
6. **Only then: expand the product**

CI proves implementation properties. It does **not** prove market need.

## Falsification is a result

When an upstream runtime fixes the original problem, human:// should record that as evidence against its own scope.

Example: OpenClaw users previously routed Slack exec approvals through another surface. Native Slack exec approvals later landed in [openclaw/openclaw#58155](https://github.com/openclaw/openclaw/pull/58155). That case no longer justifies a separate human:// approval connector.

The open question is narrower: after native runtimes provide good local approval and ownership semantics, what cross-runtime human boundaries still remain?

## Current Reality Delta needed

The next high-value signal is not another feature request.

It is one of:

- an operator with multiple runtimes/accounts uses human:// to resolve a real boundary;
- an operator says a native runtime fix is sufficient, removing a human:// use case;
- an upstream project adopts the HumanBoundary delivery/resume distinction;
- a real external integration returns an exact-request resume receipt;
- a real external user reports which boundary-integrity failure metric actually matters operationally.

Until then, features that do not improve one of those tests should be treated skeptically.
