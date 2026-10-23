---
status: draft
created: 2026-10-23
provenance: retrospective-product-review
---
# HumanQueue — product requirements draft

> Derived from the public HumanQueue README after a Devpost submission had already been made. Not a representation of a completed historical `3-prd` interview. Learner review and approval remain required.

## Core user journey
1. The operator opens the dashboard and sees a small queue of requests waiting at human boundaries.
2. They choose one request and inspect its source, reason, requested authority, and what is blocked.
3. They explicitly approve or reject the request. Neither opening the card nor ranking it counts as consent.
4. The interface reveals whether the decision was recorded, merely transported, or confirmed as returned to the originating waiting execution.

## Screens
- **Queue overview:** number of waiting boundaries; individual request cards; basic state and urgency.
- **Boundary detail:** human-readable title, source/session or runtime, bounded context, risk, permissible decisions and expiry where available.
- **Decision result:** success/failure, immutable request reference, and resume outcome with clear distinction between transport delivery and actual resume.

## Interaction and copy expectations
- Do not silently grant authority.
- Do not let a stale boundary masquerade as current.
- Do not claim that an HTTP 2xx proves a native runtime resumed.
- Show unknown ownership or uncertain resume as an explicit unknown, never an invented success.

## Presentation
Use the existing seeded dashboard and documentation illustrations as the visual reference. Preserve the currently demonstrated UI rather than inventing an unverified redesign.

## Acceptance examples
- **Happy path:** one request arrives, the operator approves it, and the same boundary is marked resolved with recorded decision.
- **Negative control:** rejection has a visible audit outcome and must not be described as successful execution.
- **Error state:** disconnected return path is distinguished from a confirmed runtime resume.

## Product decisions requiring learner review
This document records observable existing intentions; it is not evidence of earlier PRD authoring. Review with Devpost Learn `3-prd` and only then change status to `approved`.
