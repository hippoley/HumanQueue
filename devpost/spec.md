---
status: draft
created: 2026-10-23
provenance: retrospective-technical-review
---
# HumanQueue — technical specification draft

> Reconstructed from public code-facing README, not claimed to be a previously executed Devpost Learn `4-spec` session.

## Demonstrated architecture
- Python 3.10+ FastAPI service with SQLite-backed durable boundary state.
- `HumanBoundary` / `HumanQueue` compatibility API in Python; JavaScript and HTTP integrations documented in the README.
- Addressable request identity combining runtime/source context, request reference and decision lifecycle.
- A human-facing seeded demonstration at https://hippoley.github.io/PAJ-Eval/human/.

## Minimal observable request flow
1. Client creates a `human://approve` request with `source`, `ref`, `title`, `why_now` and risk/context.
2. Gateway persists and exposes the request for authorized review.
3. Human makes an explicit decision.
4. Gateway associates decision with the original boundary and attempts its supported return path.
5. Result differentiates recorded decision, transport delivery and native resume confirmation where evidence exists.

## Verification
- Manual video demonstration: new boundary → inspect context → approve or deny → inspect recorded outcome.
- Negative controls: stale decision, unknown source identity, unavailable native callback, and duplicate resolutions should not be shown as successful native resume.
- The README claims specific adapter-level tests and CI results. Independently inspect those artifacts before restating pass counts or depicting real-host integrations in the submission.
- Capture reproducible commands and a fixed code revision for any demo used as evidence.

## Scope boundary
Simulated web scenarios are not evidence of physical device actions, independent human operators, or a real authenticated model turn. Do not conflate queue visibility and machine continuation.

## Decision still pending
The learner should evaluate this retrospective technical plan with `4-spec` and amend choices where needed. Keep status `draft` until explicitly approved.
