# Positioning: authorization vs entitlement vs necessity

NeedGate should exist only if **necessity** is a distinct control primitive.

## Three different questions

| Layer | Question | Example |
| --- | --- | --- |
| Authorization | May this actor use this capability/resource? | May the agent write this repository? |
| Entitlement / scope | Did the request license this amount or area of work? | Did "fix the typo" license rewriting the parser? |
| Necessity | Does current reality still require this state transition at all? | Is the reported parser bug still present on current main? |

These layers compose but do not substitute for one another.

A proposed action can be:

- authorized but outside request scope;
- authorized and in scope but no longer necessary;
- necessary but not authorized;
- necessary and authorized but still ambiguous enough to require investigation.

## Closest neighbor: scorekeeper

scorekeeper explicitly addresses practical entitlement: an agent should not perform work that no request licensed. Its scope wall keys enforcement to externally entitled grants and measures both overreach and false restriction.

NeedGate must not duplicate that.

The distinctive NeedGate case is:

```text
user request licenses fixing issue #42
agent is authorized to edit repo
target file is in entitled scope

BUT

issue #42 is already fixed on current main
------------------------------------------
necessity = FALSE
=> ABSTAIN
```

Nothing about authorization or scope entitlement is violated. The missing fact is current-world necessity.

## Closest benchmark: FixedBench

FixedBench creates already-fixed coding tasks where no code change is required and reports that agents still modify code in a substantial fraction of cases. It also shows the mirror failure: reproduction-first prompting can over-abstain when an issue is only partially fixed.

That motivates **residual necessity**:

> What violated condition still exists in the current state, and what is the smallest intervention required to remove it?

The primitive is therefore not "permission to act" and not "scope to act". It is a current-state claim about whether an intervention remains justified.

## Closest benchmark: AgentAbstain

AgentAbstain provides a paired should-act / should-abstain critical-action boundary. NeedGate can consume that boundary for evaluation, but must not treat the gold pair label as deployable runtime evidence.

The research progression is:

```text
Stage 0  gold-backed boundary fidelity
Stage 1  current-state probes recover necessity without gold labels
Stage 2  residual-necessity probes distinguish fully-fixed vs partially-fixed
Stage 3  real runtime gate before state-changing tool calls
```

## Kill criterion

Stop this experiment if a neighboring system already provides all of the following as a first-class, model-external runtime primitive:

1. current-state necessity predicates independent of permission/scope;
2. explicit stale / unknown / conflicted evidence handling;
3. partial-fix / residual-violation semantics;
4. pre-side-effect enforcement;
5. paired measurement of unnecessary action and false abstention.

If that combination becomes standard elsewhere, the right move is upstream contribution/interoperability, not a competing project.
