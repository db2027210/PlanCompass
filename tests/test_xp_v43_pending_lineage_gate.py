#!/usr/bin/env python3
"""Offline tests for XP v43 stage-specific pending-gate routing."""

from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path
import sys


PROJECT = Path("/home/anonymous/program/resource_adaptive")
TOOLS = PROJECT / "tools"
ROOT = PROJECT / "history/xp_final_algorithm_fresh10_v38_20260803"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import xp_v38_phase_state_patch as phase
import xp_v39_exact_point_pending_trust_patch as pending
import xp_v42_automatic_transition_family_patch as v42
import xp_v43_pending_lineage_gate_patch as v43


def actual_pending_selection() -> dict:
    raw = json.loads(
        (ROOT / "batches/cont007/exact_plan_repeat/selection.json").read_text(
            encoding="utf-8"
        )
    )
    return pending.sanitize_pending_selection(raw)


def installed_phase() -> object:
    module = importlib.reload(phase)
    pending.install_phase_gate(module)
    v42.install_phase_gate(module)
    v43.install_phase_gate(module, pending)
    return module


def test_reproduces_stale_v42_role_contract_before_v43() -> None:
    module = importlib.reload(phase)
    pending.install_phase_gate(module)
    v42.install_phase_gate(module)
    gate = module._selection_gate(actual_pending_selection())
    assert gate["passed"] is False
    assert gate["checks"]["role_stage_contract"] is False


def test_v43_routes_pending_lineage_to_v39_gate() -> None:
    gate = installed_phase()._selection_gate(actual_pending_selection())
    assert gate["passed"] is True
    assert gate["selection_stage"] == "exact_point_pending_trust_second_diagnostic"
    assert gate["checks"]["v42_transition_audit_retained_as_lineage_only"] is True
    assert gate["v43_gate_routing"]["v42_role_contract_reapplied"] is False


def test_v43_does_not_relax_initial_v42_probe_gate() -> None:
    raw = json.loads(
        (ROOT / "batches/cont006/selection.json").read_text(encoding="utf-8")
    )
    module = installed_phase()
    assert module._selection_gate(raw)["passed"] is True
    mutated = copy.deepcopy(raw)
    mutated["v42_automatic_transition_family"]["resource_envelope_relaxed"] = True
    assert module._selection_gate(mutated)["passed"] is False


def test_pending_source_or_provenance_tamper_fails_closed() -> None:
    for field in (
        "manual_candidate_injection",
        "resource_envelope_relaxed",
        "parameter_step_limit_relaxed",
        "formal_range_relaxed",
    ):
        selected = actual_pending_selection()
        selected["v42_automatic_transition_family"][field] = True
        assert installed_phase()._selection_gate(selected)["passed"] is False


def main() -> int:
    tests = [
        test_reproduces_stale_v42_role_contract_before_v43,
        test_v43_routes_pending_lineage_to_v39_gate,
        test_v43_does_not_relax_initial_v42_probe_gate,
        test_pending_source_or_provenance_tamper_fails_closed,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}", flush=True)
    print(f"PASS {len(tests)}/{len(tests)} XP v43 tests", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
