from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PATH = ROOT / "experiments" / "needgate" / "interop" / "agentabstain_adapter.py"
GATE_PATH = ROOT / "experiments" / "needgate" / "gate.py"
EXAMPLES = ROOT / "experiments" / "needgate" / "interop" / "examples"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


adapter = load_module("needgate_agentabstain_adapter", ADAPTER_PATH)
gate = load_module("needgate_gate", GATE_PATH)


def test_act_pair_projects_to_act() -> None:
    bundle = adapter.load_json(EXAMPLES / "agentabstain-act.json")
    receipt = adapter.to_receipt(bundle)
    verdict, _ = gate.evaluate(receipt)
    assert verdict == "ACT"


def test_abstain_pair_projects_to_abstain() -> None:
    bundle = adapter.load_json(EXAMPLES / "agentabstain-abstain.json")
    receipt = adapter.to_receipt(bundle)
    verdict, _ = gate.evaluate(receipt)
    assert verdict == "ABSTAIN"


def test_adapter_preserves_critical_action_identity() -> None:
    bundle = adapter.load_json(EXAMPLES / "agentabstain-act.json")
    receipt = adapter.to_receipt(bundle)
    details = receipt["observations"]["intervention_is_necessary"]["details"]
    assert details["critical_actions"] == ["mcp__demo__commit_change"]
    assert details["successful_critical_calls_observed"] == ["mcp__demo__commit_change"]


def test_invalid_task_type_fails_loudly() -> None:
    bundle = adapter.load_json(EXAMPLES / "agentabstain-act.json")
    bundle["task_type"] = "maybe"
    try:
        adapter.to_receipt(bundle)
    except ValueError as exc:
        assert "unsupported task_type" in str(exc)
    else:
        raise AssertionError("expected invalid task_type to fail")
