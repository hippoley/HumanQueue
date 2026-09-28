# Claims and evidence

human:// separates **implemented code**, **verified behavior**, **real-host validation**, and **product hypotheses**.

A green checkbox here means only what the evidence column says it means.

| Claim | Status | Evidence | Current limit |
| --- | --- | --- | --- |
| Canonical local blocking path works from the packaged wheel | **cross-platform packaged-wheel E2E verified** | clean runners on Ubuntu/Python 3.10 + 3.12, macOS/Python 3.12 and Windows/Python 3.12 install the built wheel, onboard, start Gateway, block in `HumanBoundary.ask(wait=True)`, resolve the same request, then prove the original caller continues | Does not prove an external provider runtime consumes the decision |
| SDK recovers an ambiguous/lost create acknowledgement without duplicating the boundary | **packaged fault-injection E2E verified** | a real HTTP proxy lets Gateway commit/return 201, drops that successful response, then requires SDK retry with the same generated idempotency key; second response must bind to the same request with `created=false`, one canonical `created` event, and the blocked caller must still continue after human resolution | Recovery covers SDK-generated/explicit idempotency keys; callers that bypass the SDK must supply their own stable idempotency key for equivalent retry safety |
| Shipped installer entrypoints work | **verified on Ubuntu, macOS and Windows** | CI executes `scripts/install.sh` / `scripts/install.ps1` exactly as shipped, then requires config/token creation and `humanq doctor: OK` | Git/network availability and machine-specific policy can still affect end-user installs |
| Request commit cannot lose the channel publish intent | **packaged crash-recovery E2E verified** | request + `channel_outbox` intent commit atomically; a request committed before Gateway startup is leased and delivered after restart | External channels are at-least-once; crash after remote acceptance but before local completion can replay the same canonical request |
| Abandoned channel publish leases are recoverable | **verified in CI** | a second Store/worker reclaims an expired processing lease and increments attempts | Explicit channel-level `delivered=false` is audited rather than retried indefinitely |
| Concurrent Gateway workers do not double-publish one pending outbox row | **packaged multi-process E2E verified** | two independent Uvicorn/Gateway processes share one SQLite DB while a deliberately slow webhook widens the lease window; exactly one lease claim, one delivery, one completion and attempts=1 are required | At-least-once replay after a crash *after remote acceptance but before local completion* remains possible by design |
| Gateway API is bearer-token protected | **verified in CI** | gateway auth tests | Gateway token is an administrative credential, not per-human IAM |
| Channel delivery failures are observable | **verified in CI** | `channel_delivered` / `channel_undeliverable` audit tests | Delivery evidence does not change request lifecycle yet |
| Webhook resume transport is distinct from semantic resume | **verified in CI** | exact-request receipt + transport-error tests | Semantic confirmation is opt-in for webhook targets |
| Native blocking waits are not misreported as webhook failures | **verified in CI** | `resume_not_applicable` regression test | Host runtime consumption remains provider-specific |
| Missing native identity cannot dedupe or supersede decisions | **verified in CI** | A2A/OpenAI + Codex/Claude/Cursor identity tests | Duplicate visible requests are intentionally preferred to wrong reuse |
| Blank Presence / connector provenance is rejected | **verified in CI** | API boundary regression test | This does not make upstream provider ownership authoritative |
| Boundary-integrity counters are observable | **verified in CI** | `integrity_last_24h` + dashboard tests | Metrics measure human:// events, not every provider-internal failure |
| Machine/system events cannot masquerade as human decisions | **verified in CI** | human-only `required_actor_kind` gate + separate `/outcome` expiry/cancellation tests | Actor-kind truth still depends on the authenticated surface asserting provenance correctly |
| Concurrent terminal transitions have one canonical winner | **verified in CI** | independent Store instances race duplicate human decisions and human resolution vs machine expiry; terminal writes use database compare-and-set plus rollback of losing semantic side effects | SQLite is the currently verified coordination backend; distributed backends need equivalent atomic semantics |
| MCP `human_ask` blocks and returns one structured decision | **verified in CI** | MCP protocol tests | Depends on the MCP host keeping the tool invocation alive |
| Codex native PermissionRequest hook process | **packaged-hook E2E verified** | clean-wheel subprocess consumes PermissionRequest stdin, blocks on the real Gateway, preserves native session/turn identity, and emits Codex-shaped allow + deny stdout | Does not prove a Codex model turn consumed the decision |
| Codex binary discovers and trusts human:// hooks | **recurring real-binary CI verified** | CI installs `@openai/codex@latest` (0.157.1 on 2026-09-28), real `codex app-server hooks/list` discovers the hook, `humanq trust codex` writes only the reported current hash, and a second `hooks/list` must report `trusted/managed` | Still does not prove an authenticated model turn invokes the hook and resumes/denies the exact native tool call |
| Codex exact native-host proof receipt | **implemented + packaged observer verified; authenticated host receipt pending** | `humanq prove codex` ignores old pending requests, captures one new PermissionRequest, binds session/turn/tool identity, and only verifies approval after a later matching `PostToolUse`; packaged-wheel E2E preserves `tool_use_id` + `tool_response` | First authenticated Codex model turn still needs to produce a real receipt on an operator machine |
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
