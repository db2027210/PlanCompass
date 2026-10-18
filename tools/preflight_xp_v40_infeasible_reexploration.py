#!/usr/bin/env python3
"""Read-only XP v40 proof: quality gate passes, formal point is infeasible."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from pathlib import Path
import sys
from typing import Any


PROJECT = Path("/home/anonymous/program/resource_adaptive")
TOOLS = PROJECT / "tools"
BUNDLE = Path(
    "/tmp/pg_xp_unattended_20260730_1235/"
    "bundle_fix_v33_plan_quarantine"
)
ROOT = PROJECT / "history/xp_final_algorithm_fresh10_v38_20260803"
EXPECTED_STATE_SHA256 = (
    "669f60e63c4a3a775c33f76208f57ab557cdf2664bf1e26b27405eb2240dc3aa"
)
EXPECTED_SELECTION_SHA256 = (
    "c54d45696f26549c870a13719270f8aa430ad54c95d1646bb4ce1bb7b5d8bc07"
)
EXPECTED_REPLAY_SHA256 = (
    "b6644b3f064776f24f0ceed66bc1bff0fc52ee942860ed6ba4413cdd88c1d259"
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
REQUIRED_CANDIDATE_FIELDS = (
    "feature_ratio",
    "predicted_resource",
    "utilization",
    "dScore/dU",
    "T",
    "confidence",
    "g_j",
    "eta",
    "delta_param",
    "candidate_score",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"XP v40 expected JSON object: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--post-recovery", action="store_true")
    args = parser.parse_args()
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    if str(BUNDLE) not in sys.path:
        sys.path.insert(0, str(BUNDLE))

    import xp_v37_robust_state_patch as robust_state
    import xp_v38_phase_state_patch as phase_state
    import xp_v39_exact_point_pending_trust_patch as pending_trust
    import xp_v40_formal_infeasible_reexploration_patch as infeasible_reroute

    state_path = ROOT / "state.json"
    selection_path = ROOT / "batches/cont004/selection.json"
    replay_path = ROOT / "batches/cont004/replay_summary.json"
    protected_paths = [
        state_path,
        selection_path,
        replay_path,
        ROOT / "batches/cont005/score_metadata_normalization/selection.json",
        ROOT / "batches/cont005/score_metadata_normalization/audit.json",
        ROOT / "batches/cont005/formal_acceptance_infeasible_reroute.json",
    ]
    before = {str(path): path.read_bytes() for path in protected_paths}
    state = _load(state_path)
    if args.post_recovery:
        recovery = state.get("xp_v40_zero_call_infeasible_reexploration_recovery") or {}
        if (
            state.get("status") != "offline_reexploration"
            or int(state.get("next_batch_index", -1)) != 6
            or (state.get("active_phase") or {}).get("batch_id") != "cont006"
            or int((state.get("active_phase") or {}).get("physical_workload_calls", -1)) != 0
            or recovery.get("contract")
            != "xp_v40_cont005_infeasible_zero_call_schedule_recovery_v1"
            or int(recovery.get("workload_calls_added", -1)) != 0
            or recovery.get("manual_candidate_injection") is not False
        ):
            raise RuntimeError("XP v40 post-recovery scheduling state mismatch")
    elif sha256(state_path) != EXPECTED_STATE_SHA256:
        raise RuntimeError("XP v40 preflight source state SHA mismatch")
    if sha256(selection_path) != EXPECTED_SELECTION_SHA256:
        raise RuntimeError("XP v40 cont004 selection SHA mismatch")
    if sha256(replay_path) != EXPECTED_REPLAY_SHA256:
        raise RuntimeError("XP v40 cont004 replay SHA mismatch")

    selection = _load(selection_path)
    replay = _load(replay_path)
    top5 = list(selection.get("top5") or [])
    if len(top5) != 5 or any(
        any(field not in candidate for field in REQUIRED_CANDIDATE_FIELDS)
        for candidate in top5
    ):
        raise RuntimeError("XP v40 Top-5 candidate audit fields are incomplete")

    adapter = importlib.import_module("xp_unattended_adapters")
    infeasible_reroute.install_adapter(adapter)
    robust_state.install(adapter)
    phase_state.install_adapter(adapter)
    pending_trust.install_adapter(adapter)
    gate = adapter._diagnostic_entry_gate(selection_path, replay_path)
    checks = gate.get("checks") or {}
    required_checks = {
        "five_novel_noncorner_fingerprints",
        "trusted3_probe2",
        "top5_clean_no_technical_failure",
        "trusted_spearman_positive",
        "actual_best_in_predicted_top3",
        "trusted_resource_no_order_of_magnitude_error",
        "score_span_exceeds_replay_noise_bound",
        "baseline_restored",
        "diagnostic_state_neutral",
        "three_trusted_have_two_sealed_diagnostic_observations",
        "three_trusted_have_two_exact_point_clean_observations",
        "same_plan_different_params_history_forbidden",
    }
    if (
        gate.get("passed") is not True
        or not required_checks.issubset(checks)
        or any(checks[name] is not True for name in required_checks)
        or float(gate.get("trusted_spearman")) <= 0.0
        or int(gate.get("actual_best_predicted_rank", -1)) not in (1, 2, 3)
        or float(gate.get("maximum_trusted_resource_factor_error")) >= 10.0
    ):
        raise RuntimeError("XP v40 corrected formal entry gate failed")

    capability = _load(BUNDLE / "authoritative_round9/config.json").get("capability") or {}
    promoted = adapter._two_observation_formal_selection(
        selection,
        replay,
        capability,
        float(state["incumbent_score"]),
    )
    feasibility = promoted.get("formal_acceptance_feasibility_gate") or {}
    trusted_rows = [
        row
        for row in feasibility.get("candidate_bounds") or []
        if str(row.get("selection_role") or "").startswith("trusted")
    ]
    if (
        feasibility.get("passed") is not False
        or feasibility.get("feasible_trusted_ranks") != []
        or len(trusted_rows) != 3
        or any(float(row["upper_bound"]) > float(state["incumbent_score"]) + 1e-12 for row in trusted_rows)
    ):
        raise RuntimeError("XP v40 formal infeasibility proof mismatch")
    if (
        getattr(adapter, "XP_V40_FORMAL_INFEASIBLE_REEXPLORATION_CONTRACT", "")
        != infeasible_reroute.CONTRACT
    ):
        raise RuntimeError("XP v40 deterministic reroute patch is not installable")

    formal_digest = digest({name: state.get(name) for name in FORMAL_FIELDS})
    if any(path.read_bytes() != before[str(path)] for path in protected_paths):
        raise RuntimeError("XP v40 preflight mutated authoritative evidence")
    print(
        json.dumps(
            {
                "contract": "xp_v40_zero_call_infeasible_reexploration_preflight_v1",
                "status": "ready",
                "mode": "post_recovery" if args.post_recovery else "source",
                "accepted_count": int(state["accepted_count"]),
                "accepted_target": int(state["accepted_target"]),
                "trusted_spearman": gate["trusted_spearman"],
                "actual_best_predicted_rank": gate["actual_best_predicted_rank"],
                "maximum_trusted_resource_factor_error": gate[
                    "maximum_trusted_resource_factor_error"
                ],
                "entry_gate_passed": True,
                "formal_acceptance_feasible": False,
                "feasible_trusted_ranks": [],
                "next_action": "existing_deterministic_novel_explain_three_level_pipeline",
                "manual_candidate_injection": False,
                "new_workload_calls": 0,
                "formal_state_sha256": formal_digest,
                "state_unchanged": True,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
