#!/usr/bin/env python3
"""Offline XP v42 automatic-transition and zero-call recovery tests."""

from __future__ import annotations

import copy
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile


PROJECT = Path("/home/anonymous/program/resource_adaptive")
TOOLS = PROJECT / "tools"
BUNDLE = Path(
    "/tmp/pg_xp_unattended_20260730_1235/"
    "bundle_fix_v33_plan_quarantine"
)
ROOT = PROJECT / "history/xp_final_algorithm_fresh10_v38_20260803"
for path in (TOOLS, BUNDLE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import recover_xp_v42_cont006_transition_zero_call as recovery
import resume_xp_fresh0_fixed_contract_target10_v36 as v36
import xp_v38_phase_state_patch as phase
import xp_v39_exact_point_pending_trust_patch as pending
import xp_v41_excluded_ids_sealed_pool_resume_patch as v41
import xp_v42_automatic_transition_family_patch as v42


def installed_bundle() -> object:
    diagnostic = importlib.reload(importlib.import_module("xp_round10_diagnostic_bundle"))
    diagnostic = v36.install_bundle_fallback(BUNDLE)
    phase.install_bundle(diagnostic)
    v41.install_pending_bundle(diagnostic, pending)
    return diagnostic


def blocked_manifest() -> dict:
    path = ROOT / "batches/cont006/diagnostic_bundle/manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def build_kwargs(output_dir: Path) -> dict:
    manifest = blocked_manifest()
    source = manifest["source"]
    safe = manifest["diagnostic_discovery"]["safe_frontier_contract"]
    return {
        "selection_path": Path(source["selection"]),
        "replay_path": Path(source["replay"]),
        "candidate_path": Path(source["candidate_pool"]),
        "accepted_incumbent_path": Path(source["accepted_incumbent"]),
        "output_dir": output_dir,
        "extra_excluded_ids": set(source["round9_excluded_fingerprints"]),
        "risk_utilization_upper": safe["maximum_predicted_utilization"],
        "risk_audit": safe["stability_risk_audit"],
    }


def test_actual_pool_yields_six_member_strict_family_and_probe5() -> None:
    diagnostic = installed_bundle()
    with tempfile.TemporaryDirectory(prefix="xp_v42_bundle_", dir=PROJECT) as raw:
        manifest = v42.apply_automatic_transition_family(
            diagnostic,
            blocked_manifest(),
            **build_kwargs(Path(raw) / "diagnostic_bundle"),
        )
    selection = manifest["selected_selection"]
    audit = selection["v42_automatic_transition_family"]
    assert manifest["status"] == "ready"
    assert manifest["input_gaps"] == []
    assert audit["automatic_transition_family_count"] == 6
    assert audit["resource_safe_candidate_count"] == 9
    assert len(selection["top5"]) == 5
    assert len(set(audit["selected_fingerprint_ids"])) == 5
    assert [item["selection_role"] for item in selection["top5"]] == [
        "maximin_uncertain_probe"
    ] * 5
    assert sum(bool(item["clean_anchor_exploitation_probe"]) for item in selection["top5"]) == 1
    assert all(
        item["transition_search"]["source_frontier_contract"]
        == v42.APPROVED_FRONTIER_CONTRACT
        for item in selection["top5"]
    )
    assert audit["manual_candidate_injection"] is False
    assert audit["formal_state_mutation"] is False
    assert audit["new_workload_calls"] == 0


def test_phase_gate_accepts_exact_contract_and_rejects_relaxation() -> None:
    diagnostic = installed_bundle()
    with tempfile.TemporaryDirectory(prefix="xp_v42_gate_", dir=PROJECT) as raw:
        manifest = v42.apply_automatic_transition_family(
            diagnostic,
            blocked_manifest(),
            **build_kwargs(Path(raw) / "diagnostic_bundle"),
        )
    phase_module = importlib.reload(phase)
    pending.install_phase_gate(phase_module)
    v42.install_phase_gate(phase_module)
    selection = manifest["selected_selection"]
    gate = phase_module._selection_gate(selection)
    assert gate["passed"] is True
    assert gate["selection_stage"] == "automatic_safe_frontier_transition_probe5"
    mutated = copy.deepcopy(selection)
    mutated["v42_automatic_transition_family"]["resource_envelope_relaxed"] = True
    assert phase_module._selection_gate(mutated)["passed"] is False


def test_exact_cont006_wrapper_bypasses_consumed_v36_budget_and_is_idempotent() -> None:
    diagnostic = installed_bundle()
    v42.install_bundle(diagnostic)
    with tempfile.TemporaryDirectory(prefix="xp_v42_wrapper_", dir=PROJECT) as raw:
        output = Path(raw) / "campaign/batches/cont006/diagnostic_bundle"
        output.parent.mkdir(parents=True)
        shutil.copytree(ROOT / "batches/cont006/diagnostic_bundle", output)
        kwargs = build_kwargs(output)
        first = diagnostic.build_bundle(**kwargs)
        first_hashes = {
            name: v42.sha256(output / name)
            for name in ("manifest.json", "selection.json", "config_overlay.json")
        }
        second = diagnostic.build_bundle(**kwargs)
        second_hashes = {
            name: v42.sha256(output / name)
            for name in ("manifest.json", "selection.json", "config_overlay.json")
        }
        assert first["status"] == second["status"] == "ready"
        assert first_hashes == second_hashes
        assert "v36_resource_safe_maximin_fallback" not in first["selected_selection"]


def test_global_range_probe_is_not_approved_transition_family() -> None:
    candidate = {
        "formal_range_eligible": True,
        "boundary_corner": False,
        "three_level_fingerprint": {
            "id": "three-level-sha256:" + "1" * 64,
            "plan_level": "ratio-plan-sha256:" + "2" * 64,
            "valid": True,
            "validation_errors": [],
        },
        "safe_frontier_generation": {
            "contract": "bounded_global_range_probe_only_escape_v1",
            "exploration_anchor_id": "formal_incumbent",
            "maximum_relative_parameter_step": 0.8,
            "observed_maximum_relative_parameter_step": 0.4,
            "fresh_explain": True,
            "respects_formal_range": True,
            "strict_formal_range_interior": True,
            "formal_range_eligible": True,
            "trusted_without_real_evidence": False,
            "exploration_anchor_updates_accepted_incumbent_range_or_gradient": False,
        },
    }
    assert v42._approved_frontier_candidate(candidate, set()) is False


def test_zero_call_recovery_preserves_formal_state_and_archives_block() -> None:
    with tempfile.TemporaryDirectory(prefix="xp_v42_recovery_", dir=PROJECT) as raw:
        target = Path(raw) / "campaign"
        target.mkdir()
        shutil.copy2(ROOT / "state.json", target / "state.json")
        for relative in recovery.EXPECTED_ARTIFACTS:
            source = ROOT / relative
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.stat().st_size > 100_000_000:
                os.link(source, destination)
            else:
                shutil.copy2(source, destination)
        source_bundle = ROOT / "batches/cont006/diagnostic_bundle"
        shutil.copytree(source_bundle, target / "batches/cont006/diagnostic_bundle")
        before = json.loads((target / "state.json").read_text(encoding="utf-8"))
        protected_before = {
            key: copy.deepcopy(before.get(key)) for key in recovery.PROTECTED_FIELDS
        }
        result = recovery.recover(target)
        after = json.loads((target / "state.json").read_text(encoding="utf-8"))
        protected_after = {
            key: copy.deepcopy(after.get(key)) for key in recovery.PROTECTED_FIELDS
        }
        assert result["workload_calls_added"] == 0
        assert result["explain_calls_repeated"] == 0
        assert after["status"] == "offline_reexploration"
        assert after["active_phase"]["physical_workload_calls"] == 0
        assert protected_after == protected_before
        recovery._verify_bundle(
            target / "batches/cont006/diagnostic_bundle_v41_blocked_sha_b0afc740"
        )


def main() -> int:
    tests = [
        test_actual_pool_yields_six_member_strict_family_and_probe5,
        test_phase_gate_accepts_exact_contract_and_rejects_relaxation,
        test_exact_cont006_wrapper_bypasses_consumed_v36_budget_and_is_idempotent,
        test_global_range_probe_is_not_approved_transition_family,
        test_zero_call_recovery_preserves_formal_state_and_archives_block,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}", flush=True)
    print(f"PASS {len(tests)}/{len(tests)} XP v42 tests", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
