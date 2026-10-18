#!/usr/bin/env python3
"""Offline XP v41 type-contract, pool reuse, and recovery tests."""

from __future__ import annotations

import copy
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace


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

import recover_xp_v41_cont006_sealed_pool_zero_call as recovery
import xp_v38_phase_state_patch as phase
import xp_v39_exact_point_pending_trust_patch as pending
import xp_v41_excluded_ids_sealed_pool_resume_patch as v41


def candidate(identifier: str) -> dict:
    return {
        "three_level_fingerprint": {
            "id": identifier,
            "plan_level": "ratio-plan-sha256:" + "1" * 64,
        },
        "candidate_params": {"seq_page_cost": 1.0},
        "plan_regime_calibration": {"trusted_eligible": True},
        "exact_plan_regime_validated": True,
    }


def formal_snapshot(state: dict) -> dict:
    return {
        key: copy.deepcopy(state.get(key))
        for key in recovery.PROTECTED_FIELDS
    }


def test_excluded_ids_remain_strings_and_are_not_candidates() -> None:
    active = candidate("three-level-sha256:" + "a" * 64)
    corner = candidate("three-level-sha256:" + "b" * 64)
    excluded = "three-level-sha256:" + "c" * 64
    bundle = SimpleNamespace(
        build_calibration=lambda *_args: {},
        _calibrated_candidates=lambda *_args: ([copy.deepcopy(active)], [copy.deepcopy(corner)], [excluded]),
    )
    v41.install_pending_bundle(bundle, pending)
    calibration = bundle.build_calibration({"top5": []}, {"successful": []})
    actual_active, actual_corners, actual_excluded = bundle._calibrated_candidates(
        [], calibration, set()
    )
    assert len(actual_active) == 1 and len(actual_corners) == 1
    assert actual_excluded == [excluded]
    assert all(isinstance(value, str) for value in actual_excluded)


def test_malformed_excluded_ids_fail_closed() -> None:
    active = candidate("three-level-sha256:" + "d" * 64)
    bundle = SimpleNamespace(
        build_calibration=lambda *_args: {},
        _calibrated_candidates=lambda *_args: ([active], [], [123]),
    )
    v41.install_pending_bundle(bundle, pending)
    try:
        bundle._calibrated_candidates([], bundle.build_calibration({"top5": []}, {"successful": []}), set())
    except RuntimeError as exc:
        assert "excluded fingerprints" in str(exc)
    else:
        raise AssertionError("XP v41 accepted a non-string excluded ID")


def test_actual_sealed_pool_contract() -> None:
    pool = v41.validate_sealed_pool()
    assert pool["subset_count"] == 63
    assert pool["successful_subset_count"] == 63
    assert pool["workload_executed"] is False
    assert pool["predicted_safe_novel_fingerprint_count"] == 9


def test_pool_resume_calls_no_generator_and_writes_only_audit() -> None:
    old_campaign, old_pool = v41.CAMPAIGN, v41.POOL
    with tempfile.TemporaryDirectory(prefix="xp_v41_pool_", dir=PROJECT) as raw:
        campaign = Path(raw) / "campaign"
        pool = campaign / "batches/cont006/explain_only/explain_only_generation.json"
        pool.parent.mkdir(parents=True)
        os.link(old_pool, pool)
        v41.CAMPAIGN, v41.POOL = campaign, pool
        called = []
        module = SimpleNamespace(
            generate_explain_only_candidates=lambda *_args, **_kwargs: called.append(True)
        )
        v41.install_pool_resume(module)
        result = module.generate_explain_only_candidates(
            config_path=BUNDLE / "authoritative_round9/config.json",
            selection_path=BUNDLE / "authoritative_round9/selection.json",
            incumbent_path=ROOT / "batches/cont006/accepted_incumbent.json",
            query_snapshot_path=BUNDLE / "authority_refresh_accepted4_v5_20260731/query_snapshot_v3.json",
            formal_root=BUNDLE / "authoritative_round9",
            output_dir=pool.parent,
        )
        assert result["status"] == "ready"
        assert called == []
        audit = json.loads(
            (campaign / "batches/cont006/v41_sealed_pool_resume_audit.json").read_text()
        )
        assert audit["workload_calls_added"] == 0
        assert audit["explain_calls_repeated"] == 0
    v41.CAMPAIGN, v41.POOL = old_campaign, old_pool


def test_actual_corrected_bundle_reaches_next_gate_without_type_error() -> None:
    diagnostic = importlib.reload(importlib.import_module("xp_round10_diagnostic_bundle"))
    phase.install_bundle(diagnostic)
    v41.install_pending_bundle(diagnostic, pending)
    state = json.loads((ROOT / "state.json").read_text(encoding="utf-8"))
    quality = state.get("plan_regime_quarantine_audit") or {}
    risk = {
        "contract": "xp_v33_combined_plan_regime_quarantine_v1",
        "quarantined_plan_regimes": list(state.get("quarantined_plan_regimes") or []),
        "quality_quarantine": copy.deepcopy(quality),
        "quarantined_exact_parameter_fingerprints": list(
            state.get("quarantined_exact_parameter_fingerprints") or []
        ),
        "quarantined_local_regimes": list(state.get("quarantined_local_regimes") or []),
        "protocol_environment_signature_id": str(
            (state.get("incumbent") or {}).get("protocol_environment_signature_id") or ""
        ),
        "reason": "clean_exact_plan_quality_or_stability_failure",
    }
    with tempfile.TemporaryDirectory(prefix="xp_v41_bundle_") as raw:
        manifest = diagnostic.build_bundle(
            selection_path=ROOT / "batches/cont006/score_metadata_normalization/selection.json",
            replay_path=ROOT / "batches/cont004/replay_summary.json",
            candidate_path=v41.POOL,
            accepted_incumbent_path=ROOT / "batches/cont006/accepted_incumbent.json",
            output_dir=Path(raw) / "diagnostic_bundle",
            extra_excluded_ids=set(state.get("replayed_fingerprints") or []),
            risk_utilization_upper={},
            risk_audit=risk,
        )
    assert manifest["status"] == "blocked_missing_input"
    assert manifest["counts"]["novel_non_corner_fingerprints"] == 14
    assert manifest["counts"]["resource_safe_probe_candidates"] == 9
    assert manifest["counts"]["safe_transition_backtracking_candidates"] == 1
    assert manifest["counts"]["selected_top5"] == 0
    assert any("transition backtracking" in gap for gap in manifest["input_gaps"])


def test_zero_call_recovery_preserves_formal_state_and_pool() -> None:
    with tempfile.TemporaryDirectory(prefix="xp_v41_recovery_", dir=PROJECT) as raw:
        target = Path(raw) / "campaign"
        target.mkdir()
        shutil.copy2(ROOT / "state.json", target / "state.json")
        for relative in recovery.EXPECTED_ARTIFACTS:
            source = ROOT / relative
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source == v41.POOL:
                os.link(source, destination)
            else:
                shutil.copy2(source, destination)
        before = json.loads((target / "state.json").read_text(encoding="utf-8"))
        result = recovery.recover(target)
        after = json.loads((target / "state.json").read_text(encoding="utf-8"))
        assert result["workload_calls_added"] == 0
        assert result["explain_calls_repeated"] == 0
        assert after["status"] == "offline_reexploration"
        assert after["next_batch_index"] == 6
        assert after["active_phase"]["physical_workload_calls"] == 0
        assert formal_snapshot(after) == formal_snapshot(before)


def main() -> int:
    tests = [
        test_excluded_ids_remain_strings_and_are_not_candidates,
        test_malformed_excluded_ids_fail_closed,
        test_actual_sealed_pool_contract,
        test_pool_resume_calls_no_generator_and_writes_only_audit,
        test_actual_corrected_bundle_reaches_next_gate_without_type_error,
        test_zero_call_recovery_preserves_formal_state_and_pool,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}", flush=True)
    print(f"PASS {len(tests)}/{len(tests)} XP v41 tests", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
