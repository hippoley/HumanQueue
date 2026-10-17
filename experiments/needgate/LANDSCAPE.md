# Landscape checkpoint — 2026-10-08

This document exists to prevent NeedGate from drifting into an already-occupied category.

## What nearby systems already cover

| Neighbor | Primary control problem | Why NeedGate must not duplicate it |
| --- | --- | --- |
| Microsoft Agent Control Specification | deterministic fail-closed policy at intervention points | already provides pre-tool-call policy enforcement |
| Microsoft Foundry intervention points | content/security controls around input, model, tool call, tool response, output | already places guardrails in the runtime path |
| AgentAction | action authorization, stateful policy, approval, idempotency, budgets, data-flow boundaries | already owns "should this permitted tool call execute under policy?" |
| AgentGuard | zero-trust multi-phase security intervention | already owns broad runtime security gating |
| IntentGate | request-derived intent-consistency checking | already owns "is this tool call consistent with original intent?" |
| scorekeeper | practical entitlement / overreach / underreach | already owns "did the request license this scope of work?" |
| SovereignClaw | policy-constrained execution | already owns policy as a mechanical execution precondition |
| AgentAbstain | paired evaluation of should-act vs should-abstain behavior | benchmark/evaluation, not a current-state runtime necessity primitive |
| FixedBench | stale/already-fixed coding tasks plus partial-fix failure mode | benchmark/reality source, not a general runtime gate |
| SentinelBench | long-running monitoring with explicit no-op tasks | confirms no-op is a real evaluation axis, but does not define residual necessity |

## The remaining hypothesis

NeedGate should exist only for this question:

> Given that an action is authorized, in entitled scope, policy-compliant, and intent-consistent, what current violated condition still exists that makes changing state necessary now?

The durable primitive is therefore **Residual Necessity**, not generic "should I act?" reasoning.

### Residual Necessity

A state-changing intervention is justified only when:

1. a current violated condition is externally observable;
2. the observation is fresh enough for the intervention;
3. the proposed intervention is scoped to a still-violated condition;
4. no stronger current evidence shows the condition is already satisfied;
5. unresolved/conflicted evidence cannot silently become ACT.

This specifically targets cases that neighboring layers cannot distinguish:

```text
authorized?          yes
intent-consistent?   yes
within entitlement?  yes
policy-compliant?    yes
current violation?   no
------------------------
ABSTAIN
```

and:

```text
historical symptom?  fixed
residual violation?  yes (P2)
------------------------
ACT only on P2
```

## Anti-LLM-obsolescence rule

NeedGate must not depend on a model-generated "necessity score" as its authority.

Models may:
- propose hypotheses;
- choose probes;
- explain evidence;
- suggest residual invariants.

The gate's authoritative inputs must remain external observations: current revision, test result, service health, device state, API state, resource identity, timestamps, authority facts, or other inspectable environment evidence.

## Graduation metrics

A real evaluation must report at least:

- unnecessary intervention rate;
- false abstention rate;
- investigate/escalate rate;
- paired accuracy when paired cases exist;
- partial-fix / residual-violation performance separately.

A system that gets low unnecessary intervention by refusing everything has failed.

## Kill / upstream criterion

Do not spin this into an independent project if a mature neighboring system already offers all of:

- model-external current-state necessity predicates;
- explicit residual-violation semantics;
- stale/unknown/conflicted evidence states;
- pre-side-effect enforcement;
- two-sided metrics for unnecessary action and false abstention;
- a stable external interface adopted by real runtimes.

In that case, contribute upstream instead.
