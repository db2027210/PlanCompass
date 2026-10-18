#!/usr/bin/env python3
"""Read-only postlaunch audit for TPC-H v43 cycle004.

The audit proves that checkpoint replay does not spend new physical EXPLAIN
budget, that deterministic expansion precedes a semantically valid Top-5, and
that no diagnostic can be credited without that Top-5 binding.  It never
writes state and never invokes EXPLAIN or a workload.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Mapping


REQUIRED_CANDIDATE_FIELDS = {
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
}
RUNTIME_FIELDS = {
    "elapsed_wall_seconds",
    "physical_explain_invocations_this_process",
    "checkpoint_cache_hits_this_process",
    "singleflight_cache_hits_this_process",
}
LEGACY_COMPLETE_A_SHA256 = (
    "c75e3bb2c5ba6a144de0d1f089e68a60128acd7d27b6547a3a001da083f342c0"
)
LEGACY_COMPLETE_B_SHA256 = (
    "4a3fb6f8338e5b1f869459da655a8604fe418bf1ad66e48dd0b715b2bde5238c"
)
LEGACY_COMPLETE_AUDIT_SHA256 = (
    "4635ff855c6b88ec75aaf53a45ecddbb7995dea35e2ce442634b2953c6863336"
)
LEGACY_COMPLETE_CHECKPOINT_SHA256 = (
    "4d069aa00cb391a8a2d5829f1b0e7db61a6c026e93ce8cec9551d744a1d2e104"
)
LEGACY_EVIDENCE_SHA256 = (
    "50436e0d92428515b0243942e7c6daae5a6dfb7421c449d45a81033e8fbed0c1"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def normalized_expansion(value: Mapping[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(dict(value))
    bounded = result.get("bounded_expansion_audit") or {}
    for name in RUNTIME_FIELDS:
        bounded.pop(name, None)
    return result


def validate_authority(
    controller_state: Path,
    formal_state: Path,
    counter: Path,
    expected_controller_sha: str,
    expected_formal_sha: str,
    expected_counter_sha: str,
) -> Dict[str, Any]:
    for path, expected, label in (
        (controller_state, expected_controller_sha, "controller"),
        (formal_state, expected_formal_sha, "formal"),
        (counter, expected_counter_sha, "counter"),
    ):
        if not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"{label} authority SHA mismatch")
    controller = load(controller_state)
    formal = load(formal_state)
    recovery = controller.get("v42_recovery") or {}
    if not (
        int(controller.get("accepted_count", -1)) == 1
        and int(controller.get("accepted_target", -1)) == 10
        and int(controller.get("next_cycle", -1)) == 4
        and controller.get("cycles") == []
        and recovery.get("formal_state_sha256") == expected_formal_sha
        and recovery.get("accepted_counter_sha256") == expected_counter_sha
        and formal.get("status") == "complete"
        and formal.get("baseline_restored") is True
        and int(formal.get("accepted_count", -1)) == 1
        and int(formal.get("physical_call_count", -1)) == 5
        and int(formal.get("valid_replay_count", -1)) == 5
        and int(formal.get("technical_failure_count", -1)) == 0
        and counter.read_text(encoding="utf-8").strip() == "1"
    ):
        raise RuntimeError("accepted1/formal authority is not preserved")
    return controller


def validate_checkpoint(checkpoint_path: Path, formal_sha: str) -> Dict[str, Any]:
    checkpoint = load(checkpoint_path)
    results = checkpoint.get("psql_results") or {}
    live = int(checkpoint.get("live_explain_invocations", -1))
    lost = int(checkpoint.get("lost_checkpoint_results", -1))
    if not (
        checkpoint.get("schema_version")
        == "tpch_explain_expansion_checkpoint_v1"
        and (checkpoint.get("input_binding") or {}).get("resume_state_sha256")
        == formal_sha
        and isinstance(results, dict)
        and live >= 22
        and lost >= 0
        and live == len(results) + lost
        and int(
            checkpoint.get("lost_result_physical_explain_invocations", -1)
        )
        == 5
        and checkpoint.get("lost_result_outputs_present_in_cache") is True
    ):
        raise RuntimeError("checkpoint accounting or v43 repair binding mismatch")
    return checkpoint


def validate_expansion(
    first_path: Path,
    second_path: Path,
    audit_path: Path,
    formal_sha: str,
    checkpoint_path: Path | None = None,
) -> Dict[str, Any]:
    first = load(first_path)
    second = load(second_path)
    if normalized_expansion(first) != normalized_expansion(second):
        raise RuntimeError("expansion A/B semantic payloads differ")
    legacy_complete = (
        sha256(first_path) == LEGACY_COMPLETE_A_SHA256
        and sha256(second_path) == LEGACY_COMPLETE_B_SHA256
        and sha256(audit_path) == LEGACY_COMPLETE_AUDIT_SHA256
    )
    if legacy_complete:
        if checkpoint_path is None or sha256(checkpoint_path) != (
            LEGACY_COMPLETE_CHECKPOINT_SHA256
        ):
            raise RuntimeError("legacy completed expansion checkpoint SHA mismatch")
        for value in (first, second):
            bounded = value.get("bounded_expansion_audit") or {}
            calibration = value.get("formal_incumbent_calibration") or {}
            binding = bounded.get("checkpoint_input_binding") or {}
            if not (
                value.get("diagnostic_only") is True
                and value.get("explain_only") is True
                and int(value.get("new_workload_calls", -1)) == 0
                and value.get("accepted_update_allowed") is False
                and value.get("incumbent_update_allowed") is False
                and value.get("range_update_allowed") is False
                and value.get("gradient_update_allowed") is False
                and value.get("qps_used_for_inner_scoring") is False
                and len(value.get("ranked_candidates") or []) == 95
                and int(value.get("incumbent_coordinate_neighbor_count", -1))
                == 23
                and calibration.get("resume_state_sha256") == formal_sha
                and bounded.get("contract")
                == "tpch_explain_expansion_hard_budget_v1"
                and bounded.get("budget_exhausted") is True
                and bounded.get("budget_exhaustion_reason")
                == "candidate-estimation budget exhausted"
                and int(bounded.get("candidate_estimations", -1)) == 128
                and int(bounded.get("maximum_candidate_estimations", -1))
                == 128
                and int(bounded.get("maximum_explain_invocations", -1)) == 3000
                and int(
                    bounded.get("checkpoint_live_explain_invocations", -1)
                )
                == 2817
                and int(
                    bounded.get(
                        "checkpoint_prior_delivery_failure_invocations", -1
                    )
                )
                == 5
                and bounded.get(
                    "checkpoint_delivery_failure_outputs_recovered"
                )
                is True
                and int(bounded.get("explain_invocations", -1)) == 2839
                and binding.get("resume_state_sha256") == formal_sha
                and binding.get("evidence_top5_sha256")
                == LEGACY_EVIDENCE_SHA256
            ):
                raise RuntimeError("sealed legacy completed expansion is invalid")
        audit = load(audit_path)
        if not (
            audit.get("status") == "complete"
            and audit.get("deterministic_double_generation") is True
            and audit.get("checkpoint_replay_used") is True
            and audit.get("deterministic_comparison_ignored_fields")
            == ["bounded_expansion_audit.elapsed_wall_seconds"]
            and audit.get("expansion_sha256") == sha256(first_path)
            and audit.get("replayed_expansion_sha256") == sha256(second_path)
            and audit.get("checkpoint_sha256") == sha256(checkpoint_path)
            and audit.get("resume_state_sha256") == formal_sha
            and audit.get("source_evidence_sha256") == LEGACY_EVIDENCE_SHA256
            and int(audit.get("ranked_candidate_count", -1)) == 95
            and int(audit.get("incumbent_coordinate_neighbor_count", -1)) == 23
            and int(audit.get("incumbent_coordinate_above_incumbent_count", -1))
            == 5
            and int(audit.get("physical_workload_calls", -1)) == 0
            and audit.get("formal_state_mutated") is False
            and audit.get("manual_candidate_injection") is False
            and audit.get("thresholds_relaxed") is False
        ):
            raise RuntimeError("legacy completion semantic reseal failed")
        return {
            "first_sha256": sha256(first_path),
            "second_sha256": sha256(second_path),
            "candidate_estimations": 128,
            "candidate_budget_exhausted": True,
            "generation_a_checkpoint_live_explain": 2817,
            "generation_b_checkpoint_live_explain": 2817,
            "physical_explain_cumulative": 2817,
            "incumbent_coordinate_neighbor_count": 23,
            "legacy_complete_semantic_reseal": True,
            "new_explain_invocations_for_reseal": 0,
        }
    bounded_a = first.get("bounded_expansion_audit") or {}
    bounded_b = second.get("bounded_expansion_audit") or {}
    candidate_count = int(bounded_a.get("candidate_estimations", -1))
    candidate_max = int(bounded_a.get("maximum_candidate_estimations", -1))
    physical_a = int(
        bounded_a.get("physical_explain_invocations_this_process", -1)
    )
    physical_b = int(
        bounded_b.get("physical_explain_invocations_this_process", -1)
    )
    cumulative = int(
        bounded_b.get("physical_explain_budget_consumed_cumulative", -1)
    )
    physical_max = int(bounded_b.get("maximum_explain_invocations", -1))
    calibration = first.get("formal_incumbent_calibration") or {}
    if not (
        0 <= candidate_count <= candidate_max == 128
        and physical_a >= 0
        and physical_b == 0
        and 22 <= cumulative <= physical_max == 3000
        and bounded_a.get("cache_replay_consumes_new_physical_budget") is False
        and bounded_b.get("cache_replay_consumes_new_physical_budget") is False
        and int(bounded_b.get("checkpoint_cache_hits_this_process", -1))
        == int(bounded_b.get("logical_explain_requests", -2))
        and calibration.get("resume_state_sha256") == formal_sha
        and int(first.get("incumbent_coordinate_neighbor_count", -1)) >= 5
        and len(first.get("ranked_candidates") or []) >= 5
        and int(first.get("new_workload_calls", -1)) == 0
        and first.get("diagnostic_only") is True
        and first.get("explain_only") is True
    ):
        raise RuntimeError("bounded expansion/cache replay audit failed")
    if bounded_a.get("budget_exhausted") is True and not (
        candidate_count == candidate_max
        and bounded_a.get("budget_exhaustion_reason")
        == "candidate-estimation budget exhausted"
    ):
        raise RuntimeError("candidate-estimation partial boundary is inconsistent")
    audit = load(audit_path)
    if not (
        audit.get("status") == "complete"
        and audit.get("deterministic_double_generation") is True
        and audit.get("checkpoint_replay_used") is True
        and audit.get("generation_b_zero_physical_explain") is True
        and audit.get("expansion_sha256") == sha256(first_path)
        and audit.get("replayed_expansion_sha256") == sha256(second_path)
        and audit.get("resume_state_sha256") == formal_sha
        and int(audit.get("physical_workload_calls", -1)) == 0
        and audit.get("formal_state_mutated") is False
    ):
        raise RuntimeError("dynamic expansion completion audit is invalid")
    return {
        "first_sha256": sha256(first_path),
        "second_sha256": sha256(second_path),
        "candidate_estimations": candidate_count,
        "candidate_budget_exhausted": bounded_a.get("budget_exhausted"),
        "generation_a_physical_explain": physical_a,
        "generation_b_physical_explain": physical_b,
        "physical_explain_cumulative": cumulative,
        "incumbent_coordinate_neighbor_count": int(
            first["incumbent_coordinate_neighbor_count"]
        ),
    }


def validate_top5(selection_path: Path, expansion_sha: str) -> Dict[str, Any]:
    selection = load(selection_path)
    top5 = selection.get("top5") or []
    roles = [str(item.get("selection_role")) for item in top5]
    fingerprints = [
        str((item.get("three_level_fingerprint") or {}).get("id"))
        for item in top5
    ]
    missing = {
        int(item.get("rank", index + 1)): sorted(
            REQUIRED_CANDIDATE_FIELDS - set(item)
        )
        for index, item in enumerate(top5)
        if REQUIRED_CANDIDATE_FIELDS - set(item)
    }
    source_expansion = (
        (selection.get("source_evidence") or {}).get("expansion") or {}
    )
    stability_acquisition = selection.get("stability_acquisition_mode") is True
    stability_contract_ok = not stability_acquisition or (
        selection.get("selection_contract")
        == (
            "exact_structural_plan_regime_trusted3_stability_acquisition_"
            "bounded_probe2_diagnostic_v1"
        )
        and roles == ["trusted", "trusted", "trusted", "probe", "probe"]
        and selection.get("stable_trusted_ready_before_diagnostic") is False
        and selection.get("formal_admission_from_this_selection_allowed")
        is False
        and selection.get("formal_admission_requires_exact_and_robust_repeat")
        is True
        and all(
            item.get("formal_trust_requires_independent_diagnostic") is True
            for item in top5[:3]
        )
        and all(
            item.get("formal_trust_requires_independent_diagnostic") is False
            for item in top5[3:]
        )
    )
    if not (
        len(top5) == 5
        and len(set(fingerprints)) == 5
        and all(value.startswith("three-level-sha256:") for value in fingerprints)
        and all(role in {"trusted", "probe"} for role in roles)
        and not missing
        and all(item.get("non_corner_verified") is True for item in top5)
        and all(item.get("boundary_corner") is False for item in top5)
        and all(item.get("diagnostic_only") is True for item in top5)
        and all(item.get("qps_used_for_inner_scoring") is False for item in top5)
        and int(selection.get("novel_unique_fingerprint_count", -1)) >= 5
        and int(selection.get("accepted_count_start", -1)) == 1
        and int(selection.get("inherited_accepted_count", -1)) == 1
        and int(selection.get("maximum_real_replay_invocations", -1)) == 5
        and selection.get("serial_real_replay_required") is True
        and selection.get("accepted_update_allowed") is False
        and selection.get("incumbent_update_allowed") is False
        and selection.get("range_update_allowed") is False
        and selection.get("gradient_update_allowed") is False
        and selection.get("qps_used_for_inner_scoring") is False
        and source_expansion.get("sha256") == expansion_sha
        and stability_contract_ok
    ):
        raise RuntimeError(
            "probe Top-5 semantic/binding audit failed: "
            f"roles={roles} fingerprints={fingerprints} missing={missing}"
        )
    return {
        "selection_sha256": sha256(selection_path),
        "roles": roles,
        "fingerprints": fingerprints,
        "stability_acquisition_mode": stability_acquisition,
        "formal_admission_requires_repeat": stability_acquisition,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycle-root", required=True, type=Path)
    parser.add_argument("--controller-state", required=True, type=Path)
    parser.add_argument("--formal-state", required=True, type=Path)
    parser.add_argument("--accepted-counter", required=True, type=Path)
    parser.add_argument("--expected-controller-state-sha256", required=True)
    parser.add_argument("--expected-formal-state-sha256", required=True)
    parser.add_argument("--expected-counter-sha256", required=True)
    args = parser.parse_args()

    controller = validate_authority(
        args.controller_state,
        args.formal_state,
        args.accepted_counter,
        args.expected_controller_state_sha256,
        args.expected_formal_state_sha256,
        args.expected_counter_sha256,
    )
    dynamic = args.cycle_root / "dynamic_expansion_v32"
    checkpoint_path = dynamic / "explain_checkpoint.json"
    checkpoint = validate_checkpoint(
        checkpoint_path,
        args.expected_formal_state_sha256,
    )
    result: Dict[str, Any] = {
        "contract": "tpch_v43_cycle004_postlaunch_readonly_audit_v1",
        "status": "checkpoint_expansion_running",
        "accepted_count": 1,
        "formal_state_sha256": args.expected_formal_state_sha256,
        "accepted_counter_sha256": args.expected_counter_sha256,
        "controller_status": controller.get("status"),
        "checkpoint_sha256": sha256(checkpoint_path),
        "checkpoint_live_explain_invocations": int(
            checkpoint["live_explain_invocations"]
        ),
        "checkpoint_cached_result_count": len(checkpoint["psql_results"]),
        "new_workload_calls_audited": 0,
        "read_only_audit": True,
    }
    first = dynamic / "expanded_candidates.a.json"
    second = dynamic / "expanded_candidates.b.json"
    expansion_audit = dynamic / "audit.json"
    probe_a = args.cycle_root / "probe_top5.a.json"
    probe_b = args.cycle_root / "probe_top5.b.json"
    diagnostic = args.cycle_root / "diagnostic_probe"

    expansion_files = [first.exists(), second.exists(), expansion_audit.exists()]
    if any(expansion_files) and not all(expansion_files):
        if diagnostic.exists():
            raise RuntimeError("diagnostic exists before complete expansion artifacts")
        result["status"] = "checkpoint_expansion_partial"
        print(json.dumps(result, sort_keys=True))
        return 0
    if not all(expansion_files):
        if diagnostic.exists() or probe_a.exists() or probe_b.exists():
            raise RuntimeError("Top-5/diagnostic exists before complete expansion")
        print(json.dumps(result, sort_keys=True))
        return 0

    expansion = validate_expansion(
        first,
        second,
        expansion_audit,
        args.expected_formal_state_sha256,
        checkpoint_path,
    )
    result["expansion"] = expansion
    result["status"] = "expansion_complete_waiting_top5"
    if probe_a.exists() != probe_b.exists():
        raise RuntimeError("deterministic Top-5 pair is incomplete")
    if not probe_a.exists():
        if diagnostic.exists():
            raise RuntimeError("diagnostic exists before Top-5 generation")
        print(json.dumps(result, sort_keys=True))
        return 0
    if probe_a.read_bytes() != probe_b.read_bytes():
        raise RuntimeError("Top-5 deterministic double generation differs")
    top5 = validate_top5(probe_a, expansion["first_sha256"])
    result["top5"] = top5
    result["status"] = "semantic_top5_ready_before_diagnostic"
    if not diagnostic.exists():
        print(json.dumps(result, sort_keys=True))
        return 0

    state_path = diagnostic / "diagnostic_state.json"
    if not state_path.exists():
        result["status"] = "diagnostic_directory_created_after_semantic_top5"
        print(json.dumps(result, sort_keys=True))
        return 0
    state = load(state_path)
    physical = int(state.get("physical_call_count", -1))
    valid = int(state.get("valid_replay_count", -1))
    technical = int(state.get("technical_failure_count", -1))
    if not (
        state.get("selection_sha256") == top5["selection_sha256"]
        and state.get("resume_state_sha256") == args.expected_formal_state_sha256
        and int(state.get("accepted_count", -1)) == 1
        and state.get("diagnostic_only") is True
        and state.get("accepted_update_allowed") is False
        and state.get("incumbent_update_allowed") is False
        and state.get("range_update_allowed") is False
        and state.get("gradient_update_allowed") is False
        and 0 <= physical <= 5
        and 0 <= valid <= physical
        and 0 <= technical <= physical
    ):
        raise RuntimeError("diagnostic is not bound to the semantic Top-5")
    result["status"] = f"diagnostic_{state.get('status')}"
    result["diagnostic"] = {
        "selection_sha256": state["selection_sha256"],
        "physical_call_count": physical,
        "valid_replay_count": valid,
        "technical_failure_count": technical,
        "accepted_count": 1,
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
