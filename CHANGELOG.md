# Changelog

## 0.5.0 — connector runtime

- Added `NativeHandle` and bounded `ContextCapsule` connector primitives.
- Added persistent connector session/event registry and live Agent Sessions dashboard.
- Added real Codex hook connector: session observation plus native `PermissionRequest` allow/deny return.
- Added real Cursor hook connector: session observation plus conservative high-risk shell permission return.
- Added `humanq connect/disconnect codex|cursor` and `humanq sessions`.
- Added stdio MCP `human_ask` bridge so semantic clarification/review/choice returns to the same MCP tool call.
- Added generic signed webhook channel projection with independent `hqc_...` secrets and bounded context.
- Added signed third-party channel decision callback that resolves the Gateway request and resumes the native source.
- Added fail-safe native fallback when Human Queue is unavailable instead of silently authorizing.
- Added Telegram long-poll approval channel with chat authorization and inline decision buttons.\n- Added bounded dialogue enrichment so projected approvals include the latest user/agent turn without copying full transcripts.\n- Added duplicate-resolution protection so a stale/repeated button cannot resume the same workflow twice.\n- Expanded CI coverage to 32 passing tests.


## 0.4.0 — self-hosted Human Gateway

- Added `humanq onboard` to create local state and a private `hq_...` gateway token.
- Added `humanq gateway run/status`, `humanq dashboard`, `humanq doctor`, and `humanq token rotate`.
- Added Bearer-token protection for all `/v1/*` gateway APIs.
- Added token-aware Python and JavaScript SDK clients.
- Added one-command macOS/Linux/WSL installer plus Windows PowerShell installer.
- Added Docker bootstrap that generates a gateway token and persistent local volume.
- Added `docs/self-host.md` and made self-hosting the primary README quickstart.
- Added secure dashboard token handoff via URL fragment + session storage.
- Added gateway auth/token tests; suite now passes 14 tests.
- Added a public-demo “Deploy yours” flow.


## 0.3.0 — `human://`

The project stops presenting itself as an approval dashboard and becomes a small machine→human primitive.

- Added the `human://approve`, `review`, `clarify`, `auth`, `choose`, `edit`, and `claim` protocol surface.
- Added `POST /v1/human` with a small integration envelope.
- Added a synchronous Python client with optional blocking `wait()` semantics.
- Added a dependency-free JavaScript client using `fetch`.
- Added `humanq demo`, `humanq serve`, and `humanq seed` CLI commands.
- Rebuilt the UI around “what needs me now?” rather than a generic dashboard.
- Added Server-Sent Events for live queue refresh.
- Added a cross-platform wow demo: Codex, GitHub, MCP, n8n, support automation and batching.
- Added policy shadow replay: historical human choices can be tested without enabling automation.
- Added package metadata, Docker Compose, GitHub Actions CI, protocol docs, contributing guide and Apache-2.0 license.
- Test suite expanded to 11 passing tests.

## 0.2.0 — attention economics

- Added routing and quorum.
- Added request supersession.
- Added low-value decision batching.
- Added attention budgets and surface modes.
- Added delegation-frontier suggestions.
- Added attention metrics.

## 0.1.0 — attention bus prototype

- Canonical human-interrupt request model.
- Global queue, priority score, audit ledger and signed resume callbacks.
- Generic, A2A, MCP, OpenAI-style and GitHub-shaped adapters.
