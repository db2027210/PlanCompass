#!/usr/bin/env python3
"""Offline tests for XP v40's formal-infeasible re-exploration transition."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
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

import recover_xp_v40_infeasible_cont005_zero_call as recovery
import xp_v37_robust_state_patch as robust_state
import xp_v38_phase_state_patch as phase_state
import xp_v39_exact_point_pending_trust_patch as pending_trust
import xp_v40_formal_infeasible_reexploration_patch as v40


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def formal_snapshot(state: dict) -> dict:
    return {
        key: copy.deepcopy(state.get(key))
        for key in (
            "accepted_count",
            "accepted_fingerprints",
            "incumbent",
            "incumbent_score",
            "incumbent_params",
            "parameter_ranges",
            "range_state",
            "gradient_state",
        )
    }


def patched_adapter():
    adapter = importlib.reload(importlib.import_module("xp_unattended_adapters"))
    v40.install_adapter(adapter)
    robust_state.install(adapter)
    phase_state.install_adapter(adapter)
    pending_trust.install_adapter(adapter)
    return adapter


def test_actual_entry_gate_and_resource_quality_pass() -> None:
    state_before = (ROOT / "state.json").read_bytes()
    adapter = patched_adapter()
    gate = adapter._diagnostic_entry_gate(
        ROOT / "batches/cont004/selection.json",
        ROOT / "batches/cont004/replay_summary.json",
    )
    assert gate["passed"] is True
    assert gate["trusted_spearman"] > 0.0
    assert gate["actual_best_predicted_rank"] in (1, 2, 3)
    assert gate["maximum_trusted_resource_factor_error"] < 10.0
    assert gate["checks"]["three_trusted_have_two_exact_point_clean_observations"] is True
    assert (ROOT / "state.json").read_bytes() == state_before


def test_actual_formal_acceptance_is_mathematically_infeasible() -> None:
    adapter = patched_adapter()
    state = json.loads((ROOT / "state.json").read_text(encoding="utf-8"))
    selection = json.loads(
        (ROOT / "batches/cont004/selection.json").read_text(encoding="utf-8")
    )
    replay = json.loads(
        (ROOT / "batches/cont004/replay_summary.json").read_text(encoding="utf-8")
    )
    capability = json.loads(
        (BUNDLE / "authoritative_round9/config.json").read_text(encoding="utf-8")
    )["capability"]
    promoted = adapter._two_observation_formal_selection(
        selection, replay, capability, state["incumbent_score"]
    )
    gate = promoted["formal_acceptance_feasibility_gate"]
    trusted = [
        row for row in gate["candidate_bounds"]
        if row["selection_role"].startswith("trusted")
    ]
    assert gate["passed"] is False
    assert gate["feasible_trusted_ranks"] == []
    assert len(trusted) == 3
    assert all(row["upper_bound"] <= state["incumbent_score"] for row in trusted)


def test_source_patch_falls_through_without_manual_candidate() -> None:
    source = '''def route(passed):
    if not passed:
        raise ContinuationBlocked(
            "formal_acceptance_infeasible_no_fresh_lcb_above_incumbent"
        )
    return "existing_deterministic_explain"
'''
    transformed = v40.replace_terminal_block(source)
    namespace = {"ContinuationBlocked": RuntimeError}
    exec(compile(transformed, "<xp_v40_test>", "exec"), namespace)
    assert namespace["route"](False) == "existing_deterministic_explain"
    assert "candidate_params" not in transformed
    actual = v40.transformed_generate_source(importlib.reload(importlib.import_module("xp_unattended_adapters")))
    assert "formal_infeasible_routes_to_novel_explain = True" in actual
    assert "repeat_after_probe5 =" in actual
    assert v40.TERMINAL_BLOCK not in actual


def test_zero_call_recovery_preserves_formal_and_sealed_evidence() -> None:
    with tempfile.TemporaryDirectory(prefix="xp_v40_recovery_") as raw:
        target = Path(raw) / "campaign"
        target.mkdir()
        shutil.copy2(ROOT / "state.json", target / "state.json")
        for relative in recovery.EXPECTED_ARTIFACTS:
            source = ROOT / relative
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        before_state = json.loads((target / "state.json").read_text(encoding="utf-8"))
        before_artifacts = {
            relative: sha256(target / relative)
            for relative in recovery.EXPECTED_ARTIFACTS
        }
        result = recovery.recover(target)
        after_state = json.loads((target / "state.json").read_text(encoding="utf-8"))
        assert result["workload_calls_added"] == 0
        assert after_state["next_batch_index"] == 6
        assert after_state["active_phase"]["batch_id"] == "cont006"
        assert after_state["active_phase"]["physical_workload_calls"] == 0
        assert after_state["active_phase"]["manual_candidate_injection"] is False
        assert formal_snapshot(after_state) == formal_snapshot(before_state)
        assert after_state["batch_history"] == before_state["batch_history"]
        assert after_state["last_batch"] == before_state["last_batch"]
        assert after_state["replayed_fingerprints"] == before_state["replayed_fingerprints"]
        assert {
            relative: sha256(target / relative)
            for relative in recovery.EXPECTED_ARTIFACTS
        } == before_artifacts
        assert not (target / "batches/cont005/selection.json").exists()
        assert not (target / "batches/cont005/replay_summary.json").exists()


def test_recovery_fails_closed_on_any_authority_mismatch() -> None:
    with tempfile.TemporaryDirectory(prefix="xp_v40_fail_closed_") as raw:
        target = Path(raw) / "campaign"
        target.mkdir()
        shutil.copy2(ROOT / "state.json", target / "state.json")
        for relative in recovery.EXPECTED_ARTIFACTS:
            source = ROOT / relative
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        value = json.loads((target / "state.json").read_text(encoding="utf-8"))
        value["accepted_count"] = 1
        (target / "state.json").write_text(json.dumps(value), encoding="utf-8")
        try:
            recovery.recover(target)
        except RuntimeError as exc:
            assert "state SHA mismatch" in str(exc)
        else:
            raise AssertionError("XP v40 recovery accepted a mismatched authority state")


def main() -> int:
    tests = [
        test_actual_entry_gate_and_resource_quality_pass,
        test_actual_formal_acceptance_is_mathematically_infeasible,
        test_source_patch_falls_through_without_manual_candidate,
        test_zero_call_recovery_preserves_formal_and_sealed_evidence,
        test_recovery_fails_closed_on_any_authority_mismatch,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}", flush=True)
    print(f"PASS {len(tests)}/{len(tests)} XP v40 tests", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
