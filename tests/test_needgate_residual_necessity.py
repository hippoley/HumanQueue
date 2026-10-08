from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GATE_PATH = ROOT / "experiments" / "needgate" / "gate.py"
FIXTURE = ROOT / "experiments" / "needgate" / "examples" / "residual-act.json"

spec = importlib.util.spec_from_file_location("needgate_gate_residual", GATE_PATH)
assert spec and spec.loader
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def test_partial_fix_with_residual_violation_still_acts() -> None:
    receipt = gate.load(str(FIXTURE))
    verdict, reason = gate.evaluate(receipt)
    assert verdict == "ACT"
    assert "all required necessity predicates" in reason


def test_historical_failure_is_not_required_when_residual_failure_is_current() -> None:
    receipt = gate.load(str(FIXTURE))
    assert receipt["observations"]["historical_failure_still_reproduces"]["status"] == "FALSE"
    verdict, _ = gate.evaluate(receipt)
    assert verdict == "ACT"
