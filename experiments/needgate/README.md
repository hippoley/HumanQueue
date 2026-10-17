# NeedGate (experimental)

**Before an autonomous system changes the world, verify that intervention is justified by current reality.**

This experiment is intentionally narrower than generic agent safety, authorization, or human approval.

It asks one question:

> **Is a state-changing intervention necessary now?**

## Why this is a separate primitive

Existing controls often answer:

- Is the actor authenticated?
- Is the action authorized?
- Is the tool call allowed?
- Is a human approval required?
- Can the action be rolled back?

Those checks do not establish that the intervention is still needed.

A stale issue, already-fixed incident, transient failure, wrong environment, partially resolved condition, or missing prerequisite can all produce an authorized but unnecessary action.

NeedGate sits before the side-effect boundary:

```text
proposed action
      |
      v
  necessity gate
      |
      +---- ACT
      +---- ABSTAIN
      +---- INVESTIGATE
      +---- ESCALATE
      |
      v
 side effect
```

## Non-goal

NeedGate is **not** an LLM prompt that asks "should I act?".

The decision must be grounded in externally inspectable predicates: reproduction status, current state, target identity, preconditions, freshness, or other environment facts.

Models may propose probes. They do not get to manufacture the evidence that satisfies them.

## Minimal receipt

See `necessity.schema.json`.

A receipt records:

- exact proposed intervention;
- target and current-state identity;
- required predicates;
- observations for those predicates;
- whether the evidence is current enough;
- which predicates remain unknown;
- one of `ACT / ABSTAIN / INVESTIGATE / ESCALATE`;
- a machine-readable reason.

## Fail-closed semantics

The first prototype uses deliberately conservative rules:

- all required predicates true -> `ACT`;
- any required predicate false and no unresolved required predicate -> `ABSTAIN`;
- missing/stale/conflicting required evidence -> `INVESTIGATE`;
- explicit authority/human-only predicate -> `ESCALATE`.

The important invariant is that **UNKNOWN does not silently become permission to mutate state**.

## Coding example

For a coding-agent issue:

```text
predicate: issue_reproduces_on_current_main
predicate: target_revision_is_current
predicate: behavior_is_not_already_fixed
predicate: requested_scope_is_unambiguous
```

If the issue no longer reproduces, an authorized coding agent should not create a speculative patch.

## Research hypothesis

Benchmarks such as FixedBench and AgentAbstain show that capable agents often act when inaction is correct. This experiment tests a different claim:

> Can a model-external necessity receipt reduce unnecessary interventions without collapsing useful action into blanket refusal?

The project should graduate only if it produces a measurable improvement on paired ACT/ABSTAIN cases **and** preserves utility on legitimate interventions.

## Spin-out rule

This lives under HumanQueue only as an incubation surface because HumanQueue already owns the human-boundary/escalation path.

If necessity becomes a useful independent primitive, it should become its own project rather than distort `human://` into a generic policy engine.
