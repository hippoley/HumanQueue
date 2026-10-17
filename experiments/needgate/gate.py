#!/usr/bin/env python3
"""Deterministic reference evaluator for the NeedGate experiment."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


VERDICTS = {"ACT", "ABSTAIN", "INVESTIGATE", "ESCALATE"}


def load(path: str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as f:
        value = json.load(f)
    if not isinstance(value, dict):
        raise ValueError("receipt must be a JSON object")
    return value


def evaluate(receipt: dict[str, Any]) -> tuple[str, str]:
    predicates = receipt.get("predicates")
    observations = receipt.get("observations")

    if not isinstance(predicates, list) or not isinstance(observations, dict):
        return "INVESTIGATE", "missing predicates or observations"

    required = [p for p in predicates if isinstance(p, dict) and p.get("required") is True]
    if not required:
        return "INVESTIGATE", "no required necessity predicates declared"

    unresolved: list[str] = []
    false: list[str] = []

    for predicate in required:
        pid = predicate.get("id")
        if not isinstance(pid, str) or not pid:
            return "INVESTIGATE", "required predicate without stable id"

        if predicate.get("human_only") is True or (
            predicate.get("kind") == "authority" and pid not in observations
        ):
            return "ESCALATE", f"predicate {pid!r} requires human/authority resolution"

        observation = observations.get(pid)
        if not isinstance(observation, dict):
            unresolved.append(pid)
            continue

        status = observation.get("status")
        if status == "FALSE":
            false.append(pid)
        elif status in {"UNKNOWN", "CONFLICTED", "STALE", None}:
            unresolved.append(pid)
        elif status != "TRUE":
            unresolved.append(pid)

    if unresolved:
        return "INVESTIGATE", "required evidence unresolved: " + ", ".join(sorted(unresolved))

    if false:
        return "ABSTAIN", "necessity predicates false: " + ", ".join(sorted(false))

    return "ACT", "all required necessity predicates are currently witnessed"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: gate.py RECEIPT.json", file=sys.stderr)
        return 2

    try:
        receipt = load(argv[1])
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"INVESTIGATE: {exc}")
        return 2

    verdict, reason = evaluate(receipt)
    print(f"{verdict}: {reason}")
    return {
        "ACT": 0,
        "ABSTAIN": 3,
        "INVESTIGATE": 4,
        "ESCALATE": 5,
    }[verdict]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
