from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "experiments" / "needgate" / "gate.py"
EXAMPLES = ROOT / "experiments" / "needgate" / "examples"

spec = importlib.util.spec_from_file_location("needgate_experiment", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def verdict(name: str) -> str:
    receipt = module.load(str(EXAMPLES / name))
    value, _reason = module.evaluate(receipt)
    return value


def test_act_requires_all_required_predicates_true() -> None:
    assert verdict("act.json") == "ACT"


def test_false_necessity_predicate_abstains() -> None:
    assert verdict("abstain.json") == "ABSTAIN"


def test_unknown_or_stale_evidence_investigates() -> None:
    assert verdict("investigate.json") == "INVESTIGATE"


def test_missing_required_evidence_never_defaults_to_act() -> None:
    receipt = module.load(str(EXAMPLES / "act.json"))
    del receipt["observations"]["issue_reproduces"]
    value, _reason = module.evaluate(receipt)
    assert value == "INVESTIGATE"
