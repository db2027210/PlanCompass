#!/usr/bin/env python3
"""Read-only XP v42 preflight with deterministic transition-family dry build."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
from pathlib import Path
import sys
import tempfile
from typing import Any


PROJECT = Path("/home/anonymous/program/resource_adaptive")
TOOLS = PROJECT / "tools"
BUNDLE = Path(
    "/tmp/pg_xp_unattended_20260730_1235/"
    "bundle_fix_v33_plan_quarantine"
)
ROOT = PROJECT / "history/xp_final_algorithm_fresh10_v38_20260803"
EXPECTED_SOURCE_STATE_SHA256 = (
    "dee1c2a508980bbd105c006ef3d4943b5c577290a8f2ad01218446e39f2313db"
)
FORMAL_FIELDS = (
    "accepted_count",
    "accepted_fingerprints",
    "incumbent",
    "incumbent_score",
    "incumbent_params",
    "parameter_ranges",
    "range_state",
    "gradient_state",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--post-recovery", action="store_true")
    args = parser.parse_args()
    for path in (TOOLS, BUNDLE):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    import recover_xp_v42_cont006_transition_zero_call as recovery
    import xp_v38_phase_state_patch as phase
    import xp_v39_exact_point_pending_trust_patch as pending
    import xp_v41_excluded_ids_sealed_pool_resume_patch as v41
    import xp_v42_automatic_transition_family_patch as v42

    state_path = ROOT / "state.json"
    blocked_dir = ROOT / "batches/cont006/diagnostic_bundle"
    evidence = [
        state_path,
        blocked_dir / "manifest.json",
        blocked_dir / "selection.json",
        blocked_dir / "config_overlay.json",
    ] + [ROOT / relative for relative in recovery.EXPECTED_ARTIFACTS]
    before = {str(path): sha256(path) for path in evidence}
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if args.post_recovery:
        audit = state.get("xp_v42_cont006_transition_recovery") or {}
        if (
            state.get("status") != "offline_reexploration"
            or int(state.get("accepted_count", -1)) != 0
            or int(state.get("accepted_target", -1)) != 10
            or int(state.get("next_batch_index", -1)) != 6
            or (state.get("active_phase") or {}).get("batch_id") != "cont006"
            or int((state.get("active_phase") or {}).get("physical_workload_calls", -1)) != 0
            or audit.get("contract")
            != "xp_v42_cont006_automatic_transition_zero_call_recovery_v1"
            or int(audit.get("workload_calls_added", -1)) != 0
            or int(audit.get("explain_calls_repeated", -1)) != 0
        ):
            raise RuntimeError("XP v42 post-recovery state mismatch")
        recovery._verify_bundle(
            ROOT / "batches/cont006/diagnostic_bundle_v41_blocked_sha_b0afc740"
        )
    elif sha256(state_path) != EXPECTED_SOURCE_STATE_SHA256:
        raise RuntimeError("XP v42 source state SHA mismatch")
    recovery._verify_bundle(blocked_dir)
    pool = v41.validate_sealed_pool()
    manifest = json.loads((blocked_dir / "manifest.json").read_text(encoding="utf-8"))
    source = manifest["source"]
    safe = manifest["diagnostic_discovery"]["safe_frontier_contract"]
    diagnostic = importlib.reload(importlib.import_module("xp_round10_diagnostic_bundle"))
    phase.install_bundle(diagnostic)
    v41.install_pending_bundle(diagnostic, pending)
    with tempfile.TemporaryDirectory(prefix="xp_v42_preflight_", dir=PROJECT) as raw:
        fixed = v42.apply_automatic_transition_family(
            diagnostic,
            manifest,
            selection_path=Path(source["selection"]),
            replay_path=Path(source["replay"]),
            candidate_path=Path(source["candidate_pool"]),
            accepted_incumbent_path=Path(source["accepted_incumbent"]),
            output_dir=Path(raw) / "diagnostic_bundle",
            extra_excluded_ids=set(source["round9_excluded_fingerprints"]),
            risk_utilization_upper=safe["maximum_predicted_utilization"],
            risk_audit=safe["stability_risk_audit"],
        )
    phase_module = importlib.reload(phase)
    pending.install_phase_gate(phase_module)
    v42.install_phase_gate(phase_module)
    selection = fixed["selected_selection"]
    gate = phase_module._selection_gate(
        selection, state.get("replayed_fingerprints") or []
    )
    audit = selection["v42_automatic_transition_family"]
    if (
        fixed.get("status") != "ready"
        or int(audit.get("automatic_transition_family_count", -1)) != 6
        or len(selection.get("top5") or []) != 5
        or gate.get("passed") is not True
        or audit.get("manual_candidate_injection") is not False
        or audit.get("formal_state_mutation") is not False
        or int(audit.get("new_workload_calls", -1)) != 0
    ):
        raise RuntimeError("XP v42 automatic transition dry build failed")
    if any(sha256(path) != before[str(path)] for path in evidence):
        raise RuntimeError("XP v42 preflight mutated sealed evidence")
    result = {
        "contract": "xp_v42_cont006_automatic_transition_preflight_v1",
        "status": "ready",
        "mode": "post_recovery" if args.post_recovery else "source",
        "accepted_count": int(state["accepted_count"]),
        "accepted_target": int(state["accepted_target"]),
        "pool_sha256": sha256(v41.POOL),
        "pool_bytes": v41.POOL.stat().st_size,
        "subset_success": f"{pool['successful_subset_count']}/{pool['subset_count']}",
        "resource_safe_candidates": int(audit["resource_safe_candidate_count"]),
        "strict_transition_family_candidates": int(
            audit["automatic_transition_family_count"]
        ),
        "selected_top5": len(selection["top5"]),
        "selection_gate_passed": True,
        "workload_calls": 0,
        "explain_calls_repeated": 0,
        "manual_candidate_injection": False,
        "formal_state_sha256": digest(
            {key: copy.deepcopy(state.get(key)) for key in FORMAL_FIELDS}
        ),
        "state_unchanged": True,
    }
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
