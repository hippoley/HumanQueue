# Changelog

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
