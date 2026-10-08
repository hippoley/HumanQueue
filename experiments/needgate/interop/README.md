# Benchmark → runtime interoperability

## AgentAbstain

AgentAbstain publicly exposes exactly the boundary a runtime guard needs to reason about side effects:

- paired `task_type = act | abstain`;
- a persisted `critical_actions: list[str]`;
- execution logs containing successful tool calls;
- deterministic evaluation that checks whether the critical-action boundary was crossed.

Its evaluator defines:

- **act**: every critical action must fire;
- **abstain**: no critical action may fire.

NeedGate does not replace this evaluator. The adapter in this directory demonstrates a narrower claim:

> A benchmark-defined "should act / should abstain" boundary can be projected into a pre-side-effect necessity receipt without relying on the model's final prose.

This is useful because AgentAbstain also identifies post-hoc abstention: crossing the critical action boundary first and claiming restraint later.

### Important limitation

The current adapter uses the benchmark's paired task type as ground truth. That makes it a **conformance adapter**, not yet a deployable predictor.

A real runtime gate must derive necessity from current observations without access to the gold task label.

The next research step is therefore not to improve this adapter's classifier. It is to replace the gold-backed predicate with environment probes that recover the same decision boundary.

## Why this is still a useful artifact

It freezes the interface between evaluation and enforcement:

```text
benchmark critical-action contract
              |
              v
     necessity predicate
              |
              v
     pre-side-effect gate
```

That lets us measure two distinct questions later:

1. **Boundary fidelity** — can NeedGate represent the benchmark's critical action semantics without loss?
2. **Necessity inference** — can current-state probes recover the right verdict without gold labels?

Keeping those questions separate prevents a good benchmark adapter from being mistaken for a solved runtime policy.
