#!/usr/bin/env python3
"""Translate an AgentAbstain-style evaluation bundle into NeedGate observations.

This adapter intentionally consumes only public benchmark semantics:
- task_type: act | abstain
- critical_actions: list[str]
- execution_log: successful tool calls

It does not infer hidden benchmark labels from natural language and it does not
change AgentAbstain scoring. Its purpose is to prove that the benchmark's
critical-action boundary can be represented as a pre-side-effect runtime gate.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def normalize_task_type(value: Any) -> str:
    task_type = str(value).strip().lower()
    if task_type not in {"act", "abstain"}:
        raise ValueError(f"unsupported task_type: {value!r}")
    return task_type


def successful_calls(run_result: dict[str, Any]) -> set[str]:
    calls: set[str] = set()
    for entry in run_result.get("execution_log") or []:
        if not isinstance(entry, dict):
            continue
        tool = entry.get("tool")
        if entry.get("success") is True and isinstance(tool, str) and tool:
            calls.add(tool)
    return calls


def to_receipt(bundle: dict[str, Any]) -> dict[str, Any]:
    task_type = normalize_task_type(bundle.get("task_type"))
    raw_critical = bundle.get("critical_actions")
    if not isinstance(raw_critical, list) or not all(isinstance(x, str) for x in raw_critical):
        raise ValueError("critical_actions must be list[str]")

    critical = sorted(set(raw_critical))
    run_result = bundle.get("run_result")
    if not isinstance(run_result, dict):
        raise ValueError("run_result must be an object")

    calls = successful_calls(run_result)
    matched = sorted(set(critical) & calls)

    # This predicate is deliberately about the benchmark-declared action
    # boundary, not about whether the model's prose says it intended to act.
    # For should-abstain cases, necessity is false by construction of the
    # paired benchmark. For should-act cases it is true.
    necessity_status = "TRUE" if task_type == "act" else "FALSE"

    return {
        "schema_version": "0.1",
        "intervention": {
            "id": f"agentabstain:{bundle.get('category','unknown')}:{bundle.get('task_id','unknown')}",
            "kind": "benchmark_critical_action",
            "description": "Cross the AgentAbstain-declared critical action boundary",
        },
        "target": {
            "identity": f"agentabstain-task:{bundle.get('category','unknown')}:{bundle.get('task_id','unknown')}",
            "environment": {
                "benchmark": "AgentAbstain",
                "task_type": task_type,
                "action_type": str(bundle.get("action_type") or ""),
            },
        },
        "predicates": [
            {
                "id": "intervention_is_necessary",
                "required": True,
                "kind": "reality",
            }
        ],
        "observations": {
            "intervention_is_necessary": {
                "status": necessity_status,
                "source": "agentabstain:paired-task-contract",
                "details": {
                    "critical_actions": critical,
                    "successful_critical_calls_observed": matched,
                },
            }
        },
    }


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: agentabstain_adapter.py BUNDLE.json RECEIPT.json", file=sys.stderr)
        return 2

    try:
        receipt = to_receipt(load_json(argv[1]))
        Path(argv[2]).write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(argv[2])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
