#!/usr/bin/env python3
"""Audit v37, promote deterministically, and optionally launch formal work.

Without ``--armed`` this program is strictly offline: it writes a sealed
handoff package but never invokes PostgreSQL or a workload.  With ``--armed``
the same process launches the formal runner only after every postrun gate and
SHA check succeeds, removing a manual candidate-selection boundary.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
from typing import Any, Dict, Mapping

try:
    import audit_tpcc_v37_postrun_gate as gate_module
    import build_tpcc_unique_exact_validation_v6 as v6
    from tpcc_score_consistent_representative import score_consistent_representative
except ModuleNotFoundError:
    from tools import audit_tpcc_v37_postrun_gate as gate_module
    from tools import build_tpcc_unique_exact_validation_v6 as v6
    from tools.tpcc_score_consistent_representative import score_consistent_representative


def _maximum_median(scores: list[float]) -> float:
    ordered = sorted(scores)
    count = len(ordered)
    midpoint = count // 2
    if count % 2 == 0:
        return ordered[midpoint]
    return (ordered[midpoint] + ordered[midpoint + 1]) / 2.0


def promote(
    diagnostic_selection: Mapping[str, Any],
    diagnostic_ledger: Mapping[str, Any],
    postrun_gate: Mapping[str, Any],
    source_selection_path: Path,
    incumbent_score: float,
) -> Dict[str, Any]:
    if (
        postrun_gate.get("passed") is not True
        or postrun_gate.get("formal_start_authorized") is not True
        or postrun_gate.get("selection_sha256") != v6._sha256(source_selection_path)
    ):
        raise RuntimeError("v37 postrun gate does not authorize promotion")
    successful = {
        int(item["rank"]): item for item in diagnostic_ledger.get("successful") or []
    }
    if (
        int(diagnostic_ledger.get("real_workload_invocation_count", -1)) != 5
        or set(successful) != {1, 2, 3, 4, 5}
        or diagnostic_ledger.get("technical_failures") not in ([], None)
    ):
        raise RuntimeError("formal promotion requires clean v37 5/5/0 evidence")

    promoted = copy.deepcopy(dict(diagnostic_selection))
    feasibility_rows = []
    for candidate in promoted.get("top5") or []:
        rank = int(candidate["rank"])
        observation = successful[rank]
        trusted = candidate.get("selection_role") == "trusted"
        provenance = candidate.get("source_provenance") or {}
        if trusted:
            prior = list(provenance.get("trusted_observations") or [])
        else:
            prior = list(provenance.get("probe_observations_excluded_from_trusted") or [])
        if len(prior) < 2:
            raise RuntimeError(f"rank {rank} lacks two sealed role-appropriate observations")
        if (
            observation.get("workload_plan_fingerprint")
            != candidate.get("workload_plan_fingerprint")
            or (observation.get("three_level_fingerprint") or {}).get("id")
            != (candidate.get("three_level_fingerprint") or {}).get("id")
        ):
            raise RuntimeError(f"rank {rank} promotion identity mismatch")
        scores = [float(item["actual_score"]) for item in prior]
        resources = [
            {name: float(item["actual_resource"][name]) for name in v6.RESOURCES}
            for item in prior
        ]
        scores.append(float(observation["actual_score"]))
        resources.append(
            {name: float(observation["actual_resource"][name]) for name in v6.RESOURCES}
        )
        representative, robust_score, representative_audit = (
            score_consistent_representative(scores, resources, v6._score)
        )
        utilization = {
            name: representative[name] / v6.repeat_v2.CAPABILITY[name]
            for name in v6.RESOURCES
        }
        lower = min(scores)
        upper = max(scores)
        maximum_after_one = _maximum_median(scores)
        fingerprint = v6._make_fingerprint(candidate, representative, utilization)
        candidate.update(
            {
                "independent_actual_scores": scores,
                "independent_actual_resources": resources,
                "predicted_resource": representative,
                "final_resource": copy.deepcopy(representative),
                "uncalibrated_predicted_resource": copy.deepcopy(representative),
                "utilization": utilization,
                "predicted_score": robust_score,
                "candidate_score": robust_score,
                "candidate_score_recomputed": robust_score,
                "final_score": robust_score,
                "robust_acceptance_score": robust_score,
                "robust_acceptance_statistic": (
                    "median_of_role_appropriate_exact_point_actual_scores_v37"
                ),
                "formal_lcb_score": lower,
                "predicted_score_interval": {
                    "lower": lower,
                    "center": robust_score,
                    "upper": upper,
                    "source": "v37_role_separated_exact_evidence_promotion",
                },
                "maximum_robust_acceptance_score_after_one_observation": maximum_after_one,
                "three_level_fingerprint": fingerprint,
                "prediction_source": "v37_role_separated_score_consistent_promotion",
                "repeat_prediction_source": "v37_role_separated_score_consistent_promotion",
            }
        )
        candidate["confidence"] = {
            "contract": "tpcc_v37_formal_role_separated_confidence_v1",
            "measurement_count": len(scores),
            "independent_actual_scores": scores,
            "independent_repeat_complete": 1.0,
            "score_lower": lower,
            "score_median": robust_score,
            "score_upper": upper,
            "probe_rows_used_for_trusted_calibration": 0,
            "score_consistent_representative_audit": representative_audit,
        }
        calibration = copy.deepcopy(candidate.get("plan_regime_calibration") or {})
        calibration.update(
            {
                "contract": "exact_validated_plan_plus_exact_params_formal_calibration_v37",
                "actual_resource": representative,
                "actual_score": robust_score,
                "observation_count": len(scores),
                "trusted_eligible": trusted,
                "probe_rows_used_for_trusted_calibration": 0,
            }
        )
        candidate["plan_regime_calibration"] = calibration
        feasible = bool(trusted and maximum_after_one > incumbent_score + 1e-12)
        feasibility_rows.append(
            {
                "rank": rank,
                "selection_role": candidate["selection_role"],
                "three_level_fingerprint": fingerprint["id"],
                "sealed_scores": scores,
                "maximum_median_after_one_observation": maximum_after_one,
                "trusted_eligible": trusted,
                "feasible": feasible,
            }
        )

    ids = [item["three_level_fingerprint"]["id"] for item in promoted["top5"]]
    if len(set(ids)) != 5:
        raise RuntimeError("promoted formal fingerprints are not unique")
    feasible_ranks = [int(row["rank"]) for row in feasibility_rows if row["feasible"]]
    if not feasible_ranks:
        raise RuntimeError("no trusted v37 candidate can beat the incumbent median")
    promoted.update(
        {
            "schema_version": "tpcc_v37_promoted_formal_selection_v1",
            "selection_contract": "tpcc_v37_promoted_formal_exact_trusted3_probe2_v1",
            "diagnostic_only": False,
            "formal_acceptance_policy": (
                "median_of_at_least_three_independent_exact_point_actual_scores_v3"
            ),
            "formal_acceptance_feasibility_gate": {
                "contract": "trusted_only_one_additional_observation_median_upper_bound_v2",
                "incumbent_score": incumbent_score,
                "candidate_bounds": feasibility_rows,
                "feasible_ranks": feasible_ranks,
                "passed": True,
                "probe_upper_bounds_affect_gate": False,
            },
            "formal_expected_improvement_required": False,
            "formal_non_improving_candidate_policy": (
                "real_replay_rejected_without_state_update"
            ),
            "formal_admission_score": "predicted_score_interval.lower",
            "probe_observations_affect_formal_acceptance": False,
            "promoted_from_diagnostic_validation": {
                "selection_path": str(source_selection_path.resolve()),
                "selection_sha256": v6._sha256(source_selection_path),
                "quality_gate": copy.deepcopy(dict(postrun_gate)),
                "observation_count_per_candidate": min(
                    len(item["independent_actual_scores"]) for item in promoted["top5"]
                ),
            },
        }
    )
    return promoted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--formal-state", type=Path, required=True)
    parser.add_argument("--expected-formal-sha256", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--formal-batch-root", type=Path, required=True)
    parser.add_argument("--formal-runner", type=Path, required=True)
    parser.add_argument("--diagnostic-runner", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--armed", action="store_true")
    args = parser.parse_args()
    output_root = args.output_root.resolve()
    if output_root.exists():
        raise FileExistsError(f"handoff output root already exists: {output_root}")
    output_root.mkdir(parents=True)
    gate = gate_module.audit_v37(
        args.selection.resolve(),
        args.ledger.resolve(),
        args.events.resolve(),
        args.summary.resolve(),
        args.formal_state.resolve(),
        args.expected_formal_sha256,
    )
    gate_path = output_root / "postrun_gate.json"
    gate_module.durable._atomic_json(gate_path, gate)
    if gate.get("passed") is not True:
        print(json.dumps({"status": "gate_failed", "gate_path": str(gate_path)}, sort_keys=True), flush=True)
        return 3
    formal_state = v6._load(args.formal_state.resolve())
    promoted = promote(
        v6._load(args.selection.resolve()),
        v6._load(args.ledger.resolve()),
        gate,
        args.selection.resolve(),
        float(formal_state["incumbent_score"]),
    )
    formal_selection_path = output_root / "formal_top5.json"
    v6.repeat_v2.import_base().atomic_json(formal_selection_path, promoted)
    formal_selection_sha = v6._sha256(formal_selection_path)
    formal_gate = {
        "schema_version": "tpcc_v37_autonomous_formal_gate_v1",
        "passed": True,
        "formal_start_authorized": True,
        "selection_sha256": formal_selection_sha,
        "quality_gate": copy.deepcopy(gate["quality_gate"]),
        "source_diagnostic_selection_sha256": gate["selection_sha256"],
        "score_span_gate": copy.deepcopy(gate["score_span_gate"]),
        "qps_role": "outer_audit_only_not_inner_score",
    }
    formal_gate_path = output_root / "formal_gate.json"
    gate_module.durable._atomic_json(formal_gate_path, formal_gate)
    handoff = {
        "schema_version": "tpcc_v37_formal_handoff_seal_v1",
        "armed": args.armed,
        "workload_calls_added_during_preparation": 0,
        "formal_state_sha256": v6._sha256(args.formal_state.resolve()),
        "postrun_gate_sha256": v6._sha256(gate_path),
        "formal_selection_sha256": formal_selection_sha,
        "formal_gate_sha256": v6._sha256(formal_gate_path),
        "formal_runner_sha256": v6._sha256(args.formal_runner.resolve()),
        "diagnostic_runner_sha256": v6._sha256(args.diagnostic_runner.resolve()),
        "formal_batch_root": str(args.formal_batch_root.resolve()),
    }
    handoff_path = output_root / "handoff_seal.json"
    gate_module.durable._atomic_json(handoff_path, handoff)
    if not args.armed:
        print(json.dumps({"status": "prepared_not_launched", "handoff": handoff}, sort_keys=True), flush=True)
        return 0
    command = [
        sys.executable,
        "-u",
        str(args.formal_runner.resolve()),
        str(formal_selection_path),
        formal_selection_sha,
        str(formal_gate_path),
        v6._sha256(formal_gate_path),
        str(args.diagnostic_runner.resolve()),
        str(args.baseline.resolve()),
        str(args.formal_state.resolve()),
        str(args.formal_batch_root.resolve()),
    ]
    completed = subprocess.run(command, check=False, timeout=10800)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
