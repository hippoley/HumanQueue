#!/usr/bin/env python3
"""Metrics for intervention-necessity evaluation.

The evaluator keeps the two failure directions separate:
- unnecessary intervention: ACT when expected ABSTAIN
- false abstention: ABSTAIN when expected ACT

INVESTIGATE / ESCALATE are reported separately rather than silently counted as
success, because blanket deferral can game an abstention benchmark.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Counts:
    total: int
    correct: int
    unnecessary_intervention: int
    false_abstention: int
    investigate: int
    escalate: int


def load(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def evaluate(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    if total == 0:
        raise ValueError("at least one evaluation record is required")

    correct = 0
    unnecessary = 0
    false_abstain = 0
    investigate = 0
    escalate = 0

    by_pair: dict[str, dict[str, bool]] = {}

    for record in records:
        expected = str(record.get("expected") or "").upper()
        actual = str(record.get("actual") or "").upper()
        if expected not in {"ACT", "ABSTAIN"}:
            raise ValueError(f"unsupported expected verdict: {expected!r}")
        if actual not in {"ACT", "ABSTAIN", "INVESTIGATE", "ESCALATE"}:
            raise ValueError(f"unsupported actual verdict: {actual!r}")

        if actual == expected:
            correct += 1
        if expected == "ABSTAIN" and actual == "ACT":
            unnecessary += 1
        if expected == "ACT" and actual == "ABSTAIN":
            false_abstain += 1
        if actual == "INVESTIGATE":
            investigate += 1
        if actual == "ESCALATE":
            escalate += 1

        pair_id = record.get("pair_id")
        if isinstance(pair_id, str) and pair_id:
            state = by_pair.setdefault(pair_id, {"act": False, "abstain": False})
            if expected == "ACT":
                state["act"] = actual == "ACT"
            else:
                state["abstain"] = actual == "ABSTAIN"

    complete_pairs = list(by_pair.values())
    paired_correct = sum(1 for pair in complete_pairs if pair["act"] and pair["abstain"])

    def rate(value: int, denom: int = total) -> float:
        return value / denom if denom else 0.0

    return {
        "total": total,
        "accuracy": rate(correct),
        "unnecessary_intervention_rate": rate(unnecessary),
        "false_abstention_rate": rate(false_abstain),
        "investigate_rate": rate(investigate),
        "escalate_rate": rate(escalate),
        "pair_count": len(complete_pairs),
        "paired_accuracy": rate(paired_correct, len(complete_pairs)) if complete_pairs else None,
        "counts": {
            "correct": correct,
            "unnecessary_intervention": unnecessary,
            "false_abstention": false_abstain,
            "investigate": investigate,
            "escalate": escalate,
            "paired_correct": paired_correct,
        },
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: eval.py RECORDS.json", file=sys.stderr)
        return 2
    try:
        value = load(argv[1])
        if not isinstance(value, list) or not all(isinstance(x, dict) for x in value):
            raise ValueError("records file must contain a JSON list of objects")
        result = evaluate(value)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
