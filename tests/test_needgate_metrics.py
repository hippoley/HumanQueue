from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "experiments" / "needgate" / "eval.py"

spec = importlib.util.spec_from_file_location("needgate_eval", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_unnecessary_action_and_false_abstention_are_not_collapsed() -> None:
    result = module.evaluate([
        {"expected": "ABSTAIN", "actual": "ACT"},
        {"expected": "ACT", "actual": "ABSTAIN"},
    ])
    assert result["unnecessary_intervention_rate"] == 0.5
    assert result["false_abstention_rate"] == 0.5


def test_investigate_does_not_count_as_correct_abstention() -> None:
    result = module.evaluate([
        {"expected": "ABSTAIN", "actual": "INVESTIGATE"},
        {"expected": "ACT", "actual": "ACT"},
    ])
    assert result["accuracy"] == 0.5
    assert result["investigate_rate"] == 0.5


def test_paired_accuracy_requires_both_sides_correct() -> None:
    result = module.evaluate([
        {"pair_id": "a", "expected": "ACT", "actual": "ACT"},
        {"pair_id": "a", "expected": "ABSTAIN", "actual": "ABSTAIN"},
        {"pair_id": "b", "expected": "ACT", "actual": "ACT"},
        {"pair_id": "b", "expected": "ABSTAIN", "actual": "ACT"},
    ])
    assert result["pair_count"] == 2
    assert result["paired_accuracy"] == 0.5
